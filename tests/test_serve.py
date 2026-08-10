import pytest

from backend import serve as serve_module


class _FakePopen:
    """Records the command it was invoked with instead of actually
    launching uvicorn, and no-ops terminate/wait so serve_notebook's
    shutdown path can be exercised without a real subprocess."""

    instances = []

    def __init__(self, cmd, *args, **kwargs):
        self.cmd = cmd
        self.terminated = False
        self.waited_timeout = None
        _FakePopen.instances.append(self)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited_timeout = timeout


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
    _FakeObserver.instances.clear()
    yield


def _run_serve(monkeypatch, notebook_path, output_dir, port=None, compiled_calls=None):
    """serve_notebook only returns because time.sleep is patched to raise
    KeyboardInterrupt on its first call inside the `while True` loop --
    mirroring how a real user would Ctrl+C it -- so this exercises the
    full startup-through-shutdown path in one synchronous call.
    """
    if compiled_calls is None:
        compiled_calls = []

    def fake_compile_notebook(nb_path, out_dir):
        compiled_calls.append((nb_path, out_dir))

    monkeypatch.setattr(serve_module, "compile_notebook", fake_compile_notebook)
    monkeypatch.setattr(serve_module, "Observer", _FakeObserver)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(serve_module.time, "sleep", _raise_keyboard_interrupt)

    if port is None:
        serve_module.serve_notebook(str(notebook_path), str(output_dir))
    else:
        serve_module.serve_notebook(str(notebook_path), str(output_dir), port)


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


def test_serve_notebook_compiles_before_starting_the_server(tmp_path, monkeypatch):

    notebook_path = tmp_path / "nb.ipynb"
    notebook_path.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "generated"
    compiled_calls = []

    _run_serve(monkeypatch, notebook_path, output_dir, compiled_calls=compiled_calls)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


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


def test_notebook_change_handler_recompiles_on_matching_notebook_modification(tmp_path, monkeypatch):

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

    event = type("Event", (), {"src_path": str(notebook_path)})()
    handler.on_modified(event)

    assert compiled_calls == [(str(notebook_path), str(output_dir))]


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
