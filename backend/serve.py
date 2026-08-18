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

    Also reacts to on_deleted, and to on_moved's *src_path* side (the
    notebook itself moving away from the watched path, as opposed to
    dest_path above, something else moving into it) -- both the same
    underlying "the notebook this server is supposed to be watching is no
    longer at that path" condition, just reached by a hard delete instead
    of a rename. Before this, neither was handled at all either: deleting
    or renaming away the notebook mid-`serve` session (e.g. `rm
    notebook.ipynb`, a git checkout/branch switch, an editor's "move to
    trash") printed nothing and raised nothing -- the live uvicorn
    subprocess just kept serving the last successfully compiled app
    forever, with zero indication its source had disappeared, the exact
    same "orphaned compile" state GET/DELETE /api/generated
    (routes/upload.py) already surface and let an operator act on
    dashboard-side, but with no equivalent signal at all on this, the
    CLI's own live-serving side. Recovery already works once this is
    reported: recreating a notebook at the watched path fires on_created,
    which _handle_possible_notebook_change (below) already treats as an
    ordinary change and recompiles from.
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
        # temp file's name) is irrelevant here for that check.
        self._handle_possible_notebook_change(event.dest_path)
        # src_path is where the file *used to be* -- if that's the
        # notebook being watched, it just moved away from the path this
        # handler watches, the same "no longer here" condition on_deleted
        # (below) reports for a hard delete instead of a rename.
        self._handle_possible_notebook_departure(event.src_path)

    def on_deleted(self, event):
        self._handle_possible_notebook_departure(event.src_path)

    def _handle_possible_notebook_departure(self, event_path):
        # Only react to the notebook file itself disappearing from the
        # watched path -- Path.resolve() works fine here even though
        # event_path (and, on a hard delete, self.notebook_path too) no
        # longer exists on disk: with strict=False (the default), it
        # resolves symlinks/".."/"." as far as it can and appends the
        # rest unchanged, the same non-strict resolution
        # _handle_possible_notebook_change below already relies on.
        if event_path.endswith(".ipynb") and Path(event_path).resolve() == Path(self.notebook_path).resolve():
            print(
                f"\n⚠️  Notebook no longer found at {self.notebook_path} "
                "(deleted or moved). The API server keeps running the "
                "last successfully compiled version -- restore a "
                "notebook at this path to resume hot recompilation.\n"
            )

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

    # package_name is only output_dir's *basename* (e.g. "built" for
    # --output subdir/built) -- "{package_name}.app:app" below is only
    # importable by a process whose own cwd has package_name as a direct
    # child. Without an explicit cwd here, this subprocess inherited
    # whatever directory `notebook-to-api serve` itself happened to be
    # invoked from, which only ever matched by coincidence for the
    # documented default (--output "generated", a direct child of the
    # typical invocation directory). Confirmed exploitable for anything
    # else: `serve nb.ipynb --output subdir/built` compiled cleanly (compile_notebook
    # doesn't care about cwd), but the uvicorn subprocess crashed
    # immediately with "ModuleNotFoundError: No module named 'built'" --
    # "built" was never a direct child of the invocation directory, only
    # of "subdir/". That raw crash then got mis-reported one layer up:
    # the poll() loop below (see its own comment) already detects *that*
    # the subprocess exited, but folds every cause into the same "a
    # common cause is another process already listening on that port"
    # RuntimeError, actively misleading for this one.
    #
    # Setting cwd to output_dir's own parent directory makes package_name
    # a direct child of it again, regardless of how many path segments
    # --output has or whether it's relative or absolute -- and is a
    # complete no-op for the documented default: Path("generated").resolve().parent
    # is exactly the original invocation directory, identical to the
    # previous unset-cwd behavior.
    #
    # --reload-dir is a separate fix, for a distinct problem: uvicorn's
    # own --reload watcher, when no --reload-dir is given, defaults to
    # watching `Path.cwd()` recursively (confirmed against the installed
    # uvicorn's own Config.__init__: reload_dirs falls back to
    # [Path.cwd()] whenever it ends up empty) -- and cwd here is
    # output_dir's *parent*, not output_dir itself, since that's what the
    # import-resolution fix just above needs it to be. Without this,
    # `serve`'s uvicorn subprocess watched the *entire* invocation
    # directory tree for the documented default (--output "generated",
    # whose parent is the project root) -- every unrelated file in the
    # project, not just the compiled app -- triggering a spurious restart
    # on a totally unrelated edit (e.g. a test file, a doc, anything under
    # the same tree) and paying the filesystem-watch cost of a
    # potentially large, unrelated directory tree for no benefit. The
    # notebook-source watcher (NotebookChangeHandler's own Observer,
    # scheduled on just notebook_dir above) was already correctly scoped
    # this way; uvicorn's own reload watcher had no equivalent scoping
    # until now. Explicitly pointing it at output_dir itself (resolved,
    # independent of cwd) narrows it to exactly the compiled app's own
    # files, regardless of how many path segments --output has.
    server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"{package_name}.app:app",
            "--reload",
            "--reload-dir",
            str(Path(output_dir).resolve()),
            "--host",
            str(host),
            "--port",
            str(port),
        ],
        cwd=str(Path(output_dir).resolve().parent),
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

        try:

            server_process.wait(timeout=5)

        except subprocess.TimeoutExpired:

            # terminate() sends SIGTERM once, with no escalation -- a
            # child uvicorn that doesn't exit within 5s (an in-flight
            # long-running request, a slow debugger attach, a loaded
            # system, or a process that simply ignores SIGTERM) left this
            # wait() raising TimeoutExpired unhandled, which propagated
            # straight out of Ctrl+C's own documented graceful-shutdown
            # path -- the one mechanism `serve --help` tells a user to
            # rely on -- as a raw traceback instead of the "✅ Server
            # stopped." this block already prints for the common case.
            # Worse, observer.join() below (guaranteeing the notebook-
            # watcher thread has actually stopped, not just been asked
            # to) never ran either, since it sits after this whole
            # try/except, unreached by an exception escaping this block --
            # leaving the uvicorn subprocess potentially still running,
            # orphaned, after the CLI process itself had already crashed.
            # SIGKILL (unlike SIGTERM) can't be ignored or delayed by the
            # target process, so the unconditional wait() right after it
            # is bounded in practice, the same escalation any real
            # process supervisor (systemd, Docker, ...) already applies
            # after a SIGTERM grace period expires.
            print(
                "⚠️  API server did not stop within 5s after SIGTERM -- "
                "forcing it to stop.\n"
            )
            server_process.kill()
            server_process.wait()

        print("✅ Server stopped.\n")

    observer.join()
