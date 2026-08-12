import time
import subprocess
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.compiler import compile_notebook, package_name_for_output_dir
from backend.inspector import print_compile_summary


class NotebookChangeHandler(FileSystemEventHandler):
    """Watches for changes to the notebook and recompiles when modified.

    Reacts to on_modified (the file's own content is rewritten in place),
    and also on_created and on_moved. Many real-world notebook saves never
    touch the target path with an in-place write at all: they write a new
    temp file and then atomically rename it into place -- the exact same
    write-temp-then-os.replace pattern this project's own POST /api/upload
    endpoint uses (see resolve_upload_path/temp_path in
    backend/routes/upload.py), and one Jupyter's own save mechanism uses
    too, specifically to avoid ever leaving a half-written notebook on
    disk. That rename is delivered by watchdog as a FileMovedEvent (whose
    dest_path is the final notebook path, not src_path) or, on a polling
    fallback observer that can't correlate the two sides of a rename, as a
    separate FileCreatedEvent for the destination. Before this, neither
    was handled at all, so `serve`'s hot recompilation -- the entire
    point of running a live server instead of a plain `compile` -- simply
    never fired for those saves, silently, with no error to indicate
    anything had gone wrong.
    """

    def __init__(self, notebook_path, output_dir):
        self.notebook_path = notebook_path
        self.output_dir = output_dir
        self.last_compile_time = time.time()

    def on_modified(self, event):
        self._handle_possible_notebook_change(event.src_path)

    def on_created(self, event):
        self._handle_possible_notebook_change(event.src_path)

    def on_moved(self, event):
        # dest_path is where the file ends up after the rename -- the path
        # that now matches notebook_path, if anything does. src_path (the
        # temp file's name) is irrelevant here.
        self._handle_possible_notebook_change(event.dest_path)

    def _handle_possible_notebook_change(self, event_path):
        # Only react to changes to the notebook file itself
        if event_path.endswith(".ipynb") and Path(event_path).resolve() == Path(self.notebook_path).resolve():
            # Debounce: avoid multiple rapid recompiles
            current_time = time.time()
            if current_time - self.last_compile_time < 1:
                return

            self.last_compile_time = current_time

            print("\n🔄 Notebook changed. Recompiling API...")

            try:
                compile_notebook(self.notebook_path, self.output_dir)
                print("✅ Recompilation complete.")
                print_compile_summary(self.notebook_path, self.output_dir)
            except Exception as e:
                print(f"❌ Compilation error: {e}\n")


def serve_notebook(notebook_path, output_dir="generated", port=8000, host="0.0.0.0"):
    """
    Serve a notebook as a live API with hot recompilation.

    Watches the notebook for changes and automatically recompiles and
    hot-reloads the API server.

    Args:
        notebook_path: Path to the notebook file
        output_dir: Output directory for generated API (default: "generated")
        port: Port to run the API server on (default: 8000). Configurable
            so more than one notebook can be served at once -- the port
            was previously hardcoded, making that impossible without
            editing this file.
        host: Interface the API server binds to (default: "0.0.0.0").
            Previously hardcoded to "0.0.0.0" with no way to override it
            -- unlike the dashboard API server, whose bind host is
            configurable via NOTEBOOK_API_DASHBOARD_HOST for the exact
            same reason (see dashboard_host() in backend/dashboard.py):
            binding every interface is the right default for most local
            dev use, but not for a developer who wants this dev server
            reachable only from localhost (e.g. 127.0.0.1), not the whole
            LAN, without editing this file to find out that was even
            possible.
    """

    # Initial compilation
    print("📝 Initial compilation...")
    compile_notebook(notebook_path, output_dir)
    print("✅ Initial compilation complete.")
    print_compile_summary(notebook_path, output_dir)

    # Set up file watcher
    observer = Observer()
    handler = NotebookChangeHandler(notebook_path, output_dir)

    # Watch the directory containing the notebook
    notebook_dir = Path(notebook_path).parent.resolve()
    observer.schedule(handler, path=str(notebook_dir), recursive=False)
    observer.start()

    # "0.0.0.0" isn't itself a browsable address -- show "localhost" for
    # the common default (every interface, reachable via localhost too)
    # and the actual configured host otherwise, so a caller who bound to
    # a specific interface sees the address that will actually work.
    display_host = "localhost" if host == "0.0.0.0" else host

    print("🚀 Starting API server with hot reload...\n")
    print(f"📍 API: http://{display_host}:{port}")
    print(f"📍 Docs: http://{display_host}:{port}/docs")
    print(f"📍 Watch: {Path(notebook_path).resolve()}\n")
    print("Press Ctrl+C to stop.\n")

    # Start Uvicorn server with reload
    package_name = package_name_for_output_dir(output_dir)
    server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"{package_name}.app:app",
            "--reload",
            "--host",
            str(host),
            "--port",
            str(port),
        ]
    )

    try:
        while True:

            # subprocess.Popen doesn't raise or notify anything when the
            # process it started exits on its own -- without polling it
            # here, a uvicorn that dies immediately (most commonly:
            # another process already has `port` bound) left this loop
            # sleeping forever, looking like a healthy running server with
            # no indication anything had gone wrong, until the user
            # eventually gave up and hit Ctrl+C themselves.
            exit_code = server_process.poll()

            if exit_code is not None:

                observer.stop()
                observer.join()

                raise RuntimeError(
                    f"The API server exited unexpectedly (exit code "
                    f"{exit_code}) while serving on port {port}. Check the "
                    "output above for the underlying error -- a common "
                    "cause is another process already listening on that "
                    "port."
                )

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        observer.stop()
        server_process.terminate()
        server_process.wait(timeout=5)
        print("✅ Server stopped.\n")

    observer.join()
