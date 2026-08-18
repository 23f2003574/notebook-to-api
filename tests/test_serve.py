import pytest

from backend import serve as serve_module


class _FakePopen:
    """Records the command it was invoked with instead of actually
    launching uvicorn, and no-ops terminate/wait so serve_notebook's
    shutdown path can be exercised without a real subprocess.

    default_poll_returncode is a class attribute (reset in _reset_fakes)
    so a test can set it before calling serve_notebook to simulate the
    server process having already exited (e.g. `port` already in use) --
    None means "still running", matching subprocess.Popen.poll()'s own
    contract.
    """

    instances = []
    default_poll_returncode = None

    def __init__(self, cmd, *args, **kwargs):
        self.cmd = cmd
        self.cwd = kwargs.get("cwd")
        self.terminated = False
        self.waited_timeout = None
        self.poll_returncode = _FakePopen.default_poll_returncode
        _FakePopen.instances.append(self)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited_timeout = timeout

    def poll(self):
        return self.poll_returncode


class _FakeObserver:
    """Records scheduling/lifecycle calls instead of actually watching the
    filesystem, so tests don't depend on real inotify/fsevents timing."""

    instances = []

    def __init__(self):
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False
        _FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self):
        self.joined = True


def _raise_keyboard_interrupt(*args, **kwargs):
    raise KeyboardInterrupt


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakePopen.instances.clear()
    _FakePopen.default_poll_returncode = None
    _FakeObserver.instances.clear()
    yield


def _run_serve(
    monkeypatch, notebook_path, output_dir, port=None, host=None,
    compiled_calls=None, summary_calls=None,
):
    """serve_notebook only returns because time.sleep is patched to raise
    KeyboardInterrupt on its first call inside the `while True` loop --
    mirroring how a real user would Ctrl+C it -- so this exercises the
    full startup-through-shutdown path in one synchronous call.

    print_compile_summary is stubbed alongside compile_notebook (rather
    than left to run for real) because it calls inspect_notebook_data,
    which would otherwise try to actually parse these tests' placeholder
    "{}" notebook content as a real notebook and raise.
    """
    if compiled_calls is None:
        compiled_calls = []

    if summary_calls is None:
        summary_calls = []

    def fake_compile_notebook(nb_path, out_dir):
        compiled_calls.append((nb_path, out_dir))

    def fake_print_compile_summary(nb_path, out_dir):
        summary_calls.append((nb_path, out_dir))

    monkeypatch.setattr(serve_module, "compile_notebook", fake_compile_notebook)
    monkeypatch.setattr(
        serve_module, "print_compile_summary", fake_print_compile_summary
    )
    monkeypatch.setattr(serve_module, "Observer", _FakeObserver)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(serve_module.time, "sleep", _raise_keyboard_interrupt)

    kwargs = {}
    if port is not None:
        kwargs["port"] = port
    if host is not None:
        kwargs["host"] = host

    serve_module.serve_notebook(str(notebook_path), str(output_dir), **kwargs)


def test_serve_notebook_defaults_to_port_8000(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cmd[-2:] == ["--port", "8000"]


def test_serve_notebook_passes_custom_port_to_uvicorn_command(tmp_path, monkeypatch):
    """Confirmed missing before this: the port was hardcoded to 8000 in
    the uvicorn subprocess command with no way to override it, making it
    impossible to `serve` two notebooks at once.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir, port=9500)

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cmd[-2:] == ["--port", "9500"]


def test_serve_notebook_defaults_to_host_0_0_0_0(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cmd[-4:-2] == ["--host", "0.0.0.0"]


def test_serve_notebook_passes_custom_host_to_uvicorn_command(tmp_path, monkeypatch):
    """Confirmed missing before this: the host was hardcoded to "0.0.0.0"
    in the uvicorn subprocess command with no way to override it -- unlike
    the dashboard API server's bind host, which is already configurable
    via NOTEBOOK_API_DASHBOARD_HOST for the same reason (see
    dashboard_host() in backend/dashboard.py).
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir, host="127.0.0.1")

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cmd[-4:-2] == ["--host", "127.0.0.1"]


def test_serve_notebook_runs_uvicorn_from_the_output_dirs_parent_directory(
    tmp_path, monkeypatch
):
    """package_name_for_output_dir(output_dir) only ever returns
    output_dir's *basename* (e.g. "built" for a "subdir/built"
    output_dir), so "{package_name}.app:app" is only importable by a
    process whose own cwd has that basename as a direct child. Confirmed
    exploitable before this fix: `serve nb.ipynb --output subdir/built`
    compiled cleanly, but the uvicorn subprocess -- launched with no
    explicit cwd, so it inherited whatever directory `serve` itself was
    invoked from -- crashed immediately with "ModuleNotFoundError: No
    module named 'built'", since "built" was never a direct child of the
    invocation directory, only of "subdir/".
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "subdir" / "built"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cwd == str(output_dir.parent)
    assert _FakePopen.instances[0].cmd[3] == "built.app:app"


def test_serve_notebook_cwd_is_the_invocation_directory_for_the_default_output(
    tmp_path, monkeypatch
):
    """The fix above must be a complete no-op for the documented default
    (--output "generated", a direct child of wherever `serve` is
    invoked from): Path("generated").resolve().parent is exactly the
    original invocation directory, identical to the previous
    unset-cwd behavior.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    assert _FakePopen.instances[0].cwd == str(tmp_path)


def test_serve_notebook_passes_reload_dir_scoped_to_the_output_dir(tmp_path, monkeypatch):
    """uvicorn's own --reload watcher, when no --reload-dir is given,
    defaults to watching `Path.cwd()` recursively (confirmed against the
    installed uvicorn's own Config.__init__: reload_dirs falls back to
    [Path.cwd()] whenever it ends up empty) -- and cwd here is
    output_dir's *parent* (needed for `{package_name}.app:app` to be
    importable), not output_dir itself. Without an explicit --reload-dir,
    the uvicorn subprocess watched the *entire* invocation directory tree
    for the documented default (--output "generated", whose parent is the
    project root) -- every unrelated file in the project, not just the
    compiled app -- triggering a spurious restart on a totally unrelated
    edit and paying the filesystem-watch cost of a potentially large,
    unrelated directory tree for no benefit.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    cmd = _FakePopen.instances[0].cmd
    assert "--reload-dir" in cmd
    reload_dir_index = cmd.index("--reload-dir")
    assert cmd[reload_dir_index + 1] == str(output_dir.resolve())


def test_serve_notebook_scopes_reload_dir_to_output_dir_not_its_parent_for_a_nested_output(
    tmp_path, monkeypatch
):
    """Same fix as above, verified for a multi-segment --output: the
    reload watcher must stay scoped to output_dir itself even when cwd
    (output_dir's parent, for import resolution) is a different, wider
    directory than output_dir.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "subdir" / "built"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakePopen.instances) == 1
    cmd = _FakePopen.instances[0].cmd
    reload_dir_index = cmd.index("--reload-dir")
    assert cmd[reload_dir_index + 1] == str(output_dir.resolve())
    # The reload dir must be output_dir itself, not the (wider) cwd the
    # subprocess was launched from.
    assert cmd[reload_dir_index + 1] != _FakePopen.instances[0].cwd


def test_serve_notebook_prints_localhost_for_the_default_host(tmp_path, monkeypatch, capsys):
    """"0.0.0.0" isn't itself a browsable address -- the printed API/Docs
    URLs must still say "localhost" for the common default, exactly as
    they did before `host` became configurable.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    output = capsys.readouterr().out
    assert "http://localhost:8000" in output
    assert "http://0.0.0.0:8000" not in output


def test_serve_notebook_prints_the_actual_host_when_customized(tmp_path, monkeypatch, capsys):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir, host="127.0.0.1")

    output = capsys.readouterr().out
    assert "http://127.0.0.1:8000" in output


def test_serve_notebook_compiles_before_starting_the_server(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"
    compiled_calls = []

    _run_serve(monkeypatch, notebook_path, output_dir, compiled_calls=compiled_calls)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_serve_notebook_prints_a_compile_summary_after_the_initial_compile(tmp_path, monkeypatch):
    """Before this, `serve`'s initial compile gave no feedback at all
    about what had actually been generated -- just "Initial compilation
    complete." -- even though `compile` already got this exact summary
    (endpoint list, background markers, dependencies) in an earlier fix.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"
    summary_calls = []

    _run_serve(monkeypatch, notebook_path, output_dir, summary_calls=summary_calls)

    assert summary_calls == [(str(notebook_path), str(output_dir))]


def test_serve_notebook_watches_the_notebooks_parent_directory(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert len(_FakeObserver.instances) == 1
    [(handler, path, recursive)] = _FakeObserver.instances[0].scheduled
    assert isinstance(handler, serve_module.NotebookChangeHandler)
    assert path == str(tmp_path.resolve())
    assert recursive is False


def test_serve_notebook_stops_server_and_observer_on_keyboard_interrupt(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    _run_serve(monkeypatch, notebook_path, output_dir)

    assert _FakePopen.instances[0].terminated is True
    assert _FakePopen.instances[0].waited_timeout == 5
    assert _FakeObserver.instances[0].stopped is True
    assert _FakeObserver.instances[0].joined is True


def test_serve_notebook_raises_when_the_server_process_exits_unexpectedly(tmp_path, monkeypatch):
    """subprocess.Popen doesn't raise or notify anything when the process
    it started exits on its own -- before polling it in the loop, a
    uvicorn that died immediately (most commonly: another process already
    had the port bound) left serve_notebook sleeping forever, looking
    like a healthy running server with no indication anything had gone
    wrong, until the user eventually gave up and hit Ctrl+C themselves.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    monkeypatch.setattr(serve_module, "compile_notebook", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "Observer", _FakeObserver)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _FakePopen)
    # time.sleep must never be reached on this path -- the crash is
    # detected on the very first poll, before the loop ever sleeps.
    monkeypatch.setattr(
        serve_module.time, "sleep",
        lambda *a, **k: pytest.fail("must not sleep after the process has already exited")
    )

    _FakePopen.default_poll_returncode = 1

    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        serve_module.serve_notebook(str(notebook_path), str(output_dir), 8123)


def test_serve_notebook_reports_the_exit_code_and_port_when_the_server_dies(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    monkeypatch.setattr(serve_module, "compile_notebook", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "Observer", _FakeObserver)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _FakePopen)

    _FakePopen.default_poll_returncode = 1

    with pytest.raises(RuntimeError) as exc_info:
        serve_module.serve_notebook(str(notebook_path), str(output_dir), 8123)

    assert "exit code 1" in str(exc_info.value)
    assert "port 8123" in str(exc_info.value)


def test_serve_notebook_stops_and_joins_the_observer_when_the_server_dies(tmp_path, monkeypatch):
    """Cleanup must happen on this failure path too, not just the
    KeyboardInterrupt path -- otherwise the filesystem watcher thread
    from _FakeObserver's real counterpart would keep running after
    serve_notebook has already given up and raised.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    monkeypatch.setattr(serve_module, "compile_notebook", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)
    monkeypatch.setattr(serve_module, "Observer", _FakeObserver)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _FakePopen)

    _FakePopen.default_poll_returncode = 1

    with pytest.raises(RuntimeError):
        serve_module.serve_notebook(str(notebook_path), str(output_dir), 8123)

    assert _FakeObserver.instances[0].stopped is True
    assert _FakeObserver.instances[0].joined is True
    # Nothing to terminate/wait on -- the process was already dead, unlike
    # the KeyboardInterrupt shutdown path.
    assert _FakePopen.instances[0].terminated is False


def test_notebook_change_handler_recompiles_on_matching_notebook_modification(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_modified(event)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_recompiles_on_notebook_created_at_the_watched_path(
    tmp_path, monkeypatch
):
    """Some editors write a brand-new notebook to the watched path outright
    (rather than modifying an existing inode), which watchdog reports as a
    FileCreatedEvent -- distinct from the FileModifiedEvent an in-place
    write produces.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_created(event)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_recompiles_on_notebook_moved_into_place(
    tmp_path, monkeypatch
):
    """Jupyter's own save mechanism -- and this project's own POST
    /api/upload endpoint (see resolve_upload_path in
    backend/routes/upload.py) -- writes to a temp file first and then
    atomically renames it into place, to avoid ever leaving a half-written
    notebook on disk. watchdog reports that rename as a FileMovedEvent
    whose dest_path is the final notebook path; src_path is the
    now-irrelevant temp file name. Before this, on_moved wasn't handled at
    all, so this exact save pattern silently never triggered a
    recompile.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    temp_path = tmp_path / ".nb.ipynb.tmp"
    event = type(
        "Event", (), {"src_path": str(temp_path), "dest_path": str(notebook_path)}
    )()
    handler.on_moved(event)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_ignores_an_unrelated_file_moved_into_the_directory(
    tmp_path, monkeypatch
):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    temp_path = tmp_path / ".other.ipynb.tmp"
    other_path = tmp_path / "other.ipynb"
    event = type(
        "Event", (), {"src_path": str(temp_path), "dest_path": str(other_path)}
    )()
    handler.on_moved(event)

    assert compiled_calls == []


def test_notebook_change_handler_warns_when_the_notebook_is_deleted(
    tmp_path, monkeypatch, capsys
):
    """Before this, deleting the notebook mid-`serve` session (e.g. `rm
    notebook.ipynb`, a git checkout/branch switch) printed nothing and
    raised nothing at all -- the live uvicorn subprocess just kept
    serving the last successfully compiled app forever, with zero
    indication its source had disappeared.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    notebook_path.unlink()
    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_deleted(event)

    # A deletion is never a recompile-able change.
    assert compiled_calls == []

    captured = capsys.readouterr()
    assert "no longer found" in captured.out
    assert str(notebook_path) in captured.out


def test_notebook_change_handler_ignores_deletion_of_an_unrelated_file(
    tmp_path, monkeypatch, capsys
):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    other_path = tmp_path / "other.ipynb"
    other_path.write_text("{}", encoding="utf-8")
    other_path.unlink()

    event = type("Event", (), {"src_path": str(other_path)})()
    handler.on_deleted(event)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_notebook_change_handler_warns_when_the_notebook_is_moved_away(
    tmp_path, monkeypatch, capsys
):
    """The same "notebook no longer at the watched path" condition as a
    hard delete above, just reached by renaming the watched notebook
    itself away instead -- on_moved's existing dest_path check only
    catches something new arriving at notebook_path, not notebook_path's
    own content leaving it.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    renamed_path = tmp_path / "nb_renamed_away.ipynb"
    notebook_path.rename(renamed_path)

    event = type(
        "Event", (), {"src_path": str(notebook_path), "dest_path": str(renamed_path)}
    )()
    handler.on_moved(event)

    assert compiled_calls == []

    captured = capsys.readouterr()
    assert "no longer found" in captured.out


def test_notebook_change_handler_recovers_after_the_notebook_is_recreated(
    tmp_path, monkeypatch, capsys
):
    """Recovery from the "deleted" warning above needs no separate code
    path: recreating a notebook at the watched path fires on_created,
    which _handle_possible_notebook_change already treats as an ordinary
    change and recompiles from.
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    notebook_path.unlink()
    handler.on_deleted(type("Event", (), {"src_path": str(notebook_path)})())
    capsys.readouterr()

    notebook_path.write_text("{}", encoding="utf-8")
    handler.last_compile_time = 0
    handler.on_created(type("Event", (), {"src_path": str(notebook_path)})())

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_prints_a_compile_summary_after_recompiling(tmp_path, monkeypatch):
    """A live `serve` session's entire point is a fast, informative
    feedback loop after every save -- before this, a hot-recompile gave
    no indication at all of what had changed, just "Recompilation
    complete."
    """

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    monkeypatch.setattr(serve_module, "compile_notebook", lambda nb, out: None)

    summary_calls = []
    monkeypatch.setattr(
        serve_module, "print_compile_summary",
        lambda nb, out: summary_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_modified(event)

    assert summary_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_does_not_print_a_summary_when_recompilation_fails(
    tmp_path, monkeypatch
):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    def fake_compile_notebook(nb, out):
        raise ValueError("boom")

    monkeypatch.setattr(serve_module, "compile_notebook", fake_compile_notebook)

    summary_calls = []
    monkeypatch.setattr(
        serve_module, "print_compile_summary",
        lambda nb, out: summary_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_modified(event)  # must not raise

    assert summary_calls == []


def test_notebook_change_handler_ignores_a_different_notebook_file(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    other_path = tmp_path / "other.ipynb"
    other_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(other_path)})()
    handler.on_modified(event)

    assert compiled_calls == []


def test_notebook_change_handler_ignores_non_ipynb_modification(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(tmp_path / "nb.ipynb.swp")})()
    handler.on_modified(event)

    assert compiled_calls == []


def test_notebook_change_handler_debounces_rapid_modifications(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    compiled_calls = []
    monkeypatch.setattr(
        serve_module, "compile_notebook",
        lambda nb, out: compiled_calls.append((nb, out))
    )
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda nb, out: None)

    fake_now = [100.0]
    monkeypatch.setattr(serve_module.time, "time", lambda: fake_now[0])

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 100.0

    event = type("Event", (), {"src_path": str(notebook_path)})()

    fake_now[0] = 100.5  # within the 1-second debounce window
    handler.on_modified(event)
    assert compiled_calls == []

    fake_now[0] = 101.5  # past the debounce window
    handler.on_modified(event)
    assert compiled_calls == [(str(notebook_path), str(output_dir))]


def test_notebook_change_handler_reports_compilation_errors_without_raising(tmp_path, monkeypatch, capsys):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"

    def fake_compile_notebook(nb, out):
        raise ValueError("boom")

    monkeypatch.setattr(serve_module, "compile_notebook", fake_compile_notebook)

    handler = serve_module.NotebookChangeHandler(str(notebook_path), str(output_dir))
    handler.last_compile_time = 0

    event = type("Event", (), {"src_path": str(notebook_path)})()

    handler.on_modified(event)  # must not raise

    captured = capsys.readouterr()
    assert "boom" in captured.out
