import time
import subprocess
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.compiler import compile_notebook


class NotebookChangeHandler(FileSystemEventHandler):
    """Watches for changes to the notebook and recompiles when modified."""

    def __init__(self, notebook_path, output_dir):
        self.notebook_path = notebook_path
        self.output_dir = output_dir
        self.last_compile_time = time.time()

    def on_modified(self, event):
        # Only react to modifications of the notebook file itself
        if event.src_path.endswith(".ipynb") and Path(event.src_path).resolve() == Path(self.notebook_path).resolve():
            # Debounce: avoid multiple rapid recompiles
            current_time = time.time()
            if current_time - self.last_compile_time < 1:
                return

            self.last_compile_time = current_time

            print("\n🔄 Notebook changed. Recompiling API...")

            try:
                compile_notebook(self.notebook_path, self.output_dir)
                print("✅ Recompilation complete.\n")
            except Exception as e:
                print(f"❌ Compilation error: {e}\n")


def serve_notebook(notebook_path, output_dir="generated"):
    """
    Serve a notebook as a live API with hot recompilation.

    Watches the notebook for changes and automatically recompiles and
    hot-reloads the API server.

    Args:
        notebook_path: Path to the notebook file
        output_dir: Output directory for generated API (default: "generated")
    """

    # Initial compilation
    print("📝 Initial compilation...")
    compile_notebook(notebook_path, output_dir)
    print("✅ Initial compilation complete.\n")

    # Set up file watcher
    observer = Observer()
    handler = NotebookChangeHandler(notebook_path, output_dir)

    # Watch the directory containing the notebook
    notebook_dir = Path(notebook_path).parent.resolve()
    observer.schedule(handler, path=str(notebook_dir), recursive=False)
    observer.start()

    print("🚀 Starting API server with hot reload...\n")
    print(f"📍 API: http://localhost:8000")
    print(f"📍 Docs: http://localhost:8000/docs")
    print(f"📍 Watch: {Path(notebook_path).resolve()}\n")
    print("Press Ctrl+C to stop.\n")

    # Start Uvicorn server with reload
    server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "generated.app:app",
            "--reload",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
    )

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        observer.stop()
        server_process.terminate()
        server_process.wait(timeout=5)
        print("✅ Server stopped.\n")

    observer.join()
