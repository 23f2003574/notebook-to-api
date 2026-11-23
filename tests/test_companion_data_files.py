"""A notebook imported from a zip keeps the data and model files that travel with it, and ships them."""
import io
import json
import stat
import zipfile
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)

READER = (
    "import json\n"
    "def weights() -> dict:\n"
    "    return json.load(open('weights.json'))\n"
    "def nested() -> str:\n"
    "    return open('models/vocab.txt').read()\n"
)


def _nb(src=READER):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(src))
    return nbformat.writes(nb).encode()


def _zip(entries, symlinks=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
        for name, target in symlinks:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _import(data, overwrite=False):
    r = client.post("/api/notebooks/import", params={"overwrite": str(overwrite).lower()},
                    files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_companion_files_are_stored_per_notebook_with_their_relative_paths(env):
    results = _import(_zip([("m.ipynb", _nb()), ("weights.json", '{"w": 1}'), ("models/vocab.txt", "a b c")]))
    assert results[0]["status"] == "success"
    assert {f["path"] for f in results[0]["companion_files"]} == {"weights.json", "models/vocab.txt"}
    assert results[0]["companion_skipped"] == []
    listing = client.get("/api/notebooks/m.ipynb/files").json()
    assert listing["total_count"] == 2 and listing["total_bytes"] == len('{"w": 1}') + len("a b c")
    assert (env / "uploads" / ".m.ipynb.files" / "models" / "vocab.txt").read_text() == "a b c"
    assert not (env / "uploads" / "weights.json").exists()  # nothing lands in the shared folder


def test_the_compile_ships_the_files_and_the_generated_app_reads_them(env, monkeypatch):
    _import(_zip([("m.ipynb", _nb()), ("weights.json", '{"w": 7}'), ("models/vocab.txt", "hello")]))
    compiled = client.post("/api/compile", json={"notebook_path": "m.ipynb", "per_notebook": True})
    assert compiled.status_code == 200, compiled.text
    assert not any("doesn't exist" in w for w in compiled.json()["startup_warnings"])
    app_dir = next((env / "apps").iterdir())
    runtime = app_dir / "runtime"
    assert (runtime / "weights.json").read_text() == '{"w": 7}' and (runtime / "models" / "vocab.txt").read_text() == "hello"

    import importlib
    import sys

    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")
    monkeypatch.syspath_prepend(str(app_dir.parent))
    module = importlib.import_module(f"{app_dir.name}.app")
    try:
        answer = TestClient(module.app).post("/weights", json={}, headers={"X-API-Key": "k"})
        assert answer.json() == {"result": {"w": 7}}
        assert TestClient(module.app).post("/nested", json={}, headers={"X-API-Key": "k"}).json() == {"result": "hello"}
    finally:
        for name in [n for n in sys.modules if n == app_dir.name or n.startswith(app_dir.name + ".")]:
            del sys.modules[name]


def test_without_companion_files_the_missing_file_is_still_reported(env):
    client.post("/api/upload", files={"file": ("m.ipynb", _nb(), "application/json")})
    body = client.post("/api/compile", json={"notebook_path": "m.ipynb", "per_notebook": True}).json()
    assert any("weights.json" in w and "exist" in w for w in body["startup_warnings"])


def test_two_notebooks_with_the_same_file_name_stay_isolated(env):
    reader = "import json\ndef value() -> int:\n    return json.load(open('data.json'))['v']\n"
    _import(_zip([("one/a.ipynb", _nb(reader)), ("one/data.json", '{"v": 1}'), ("two/b.ipynb", _nb(reader)), ("two/data.json", '{"v": 2}')]))
    assert (env / "uploads" / ".a.ipynb.files" / "data.json").read_text() == '{"v": 1}'
    assert (env / "uploads" / ".b.ipynb.files" / "data.json").read_text() == '{"v": 2}'
    for name in ("a.ipynb", "b.ipynb"):
        assert client.post("/api/compile", json={"notebook_path": name, "per_notebook": True}).status_code == 200
    contents = sorted((d / "runtime" / "data.json").read_text() for d in (env / "apps").iterdir())
    assert contents == ['{"v": 1}', '{"v": 2}']


@pytest.mark.parametrize("member", ["../evil.json", "/abs.json", "a/../../b.json", "..\\win.json"])
def test_traversal_names_are_skipped_and_nothing_escapes(env, member):
    results = _import(_zip([("m.ipynb", _nb()), (member, "x"), ("weights.json", "{}")]))
    assert results[0]["status"] == "success"
    assert all(s["reason"] == "unsafe path" for s in results[0]["companion_skipped"]) or results[0]["companion_skipped"] == []
    assert not (env / "evil.json").exists() and not (env.parent / "evil.json").exists() and not (env / "b.json").exists()
    assert [f["path"] for f in results[0]["companion_files"]] == ["weights.json"]


def test_symlinks_unsupported_types_duplicates_and_oversize_are_reported_not_stored(env, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_TO_API_MAX_DATA_MB", "1")
    big = b"x" * (2 * 1024 * 1024)
    results = _import(_zip(
        [("m.ipynb", _nb()), ("weights.json", "{}"), ("Weights.JSON", "{}"), ("run.exe", "MZ"), ("big.csv", big)],
        symlinks=[("link.json", "/etc/passwd")],
    ))
    skipped = {s["path"]: s["reason"] for s in results[0]["companion_skipped"]}
    assert "symbolic" in skipped["link.json"] and "unsupported" in skipped["run.exe"]
    assert "duplicate" in skipped["Weights.JSON"] and "limit" in skipped["big.csv"]
    assert [f["path"] for f in results[0]["companion_files"]] == ["weights.json"]
    assert not (env / "uploads" / ".m.ipynb.files" / "link.json").exists()


def test_invalid_archives_and_dry_runs_store_nothing(env):
    assert client.post("/api/notebooks/import", files={"file": ("x.zip", b"not a zip", "application/zip")}).status_code == 400
    dry = client.post("/api/notebooks/import", params={"dry_run": "true"},
                      files={"file": ("x.zip", _zip([("m.ipynb", _nb()), ("weights.json", "{}")]), "application/zip")}).json()
    assert dry["results"][0]["companion_files"] == [{"path": "weights.json", "bytes": 2}]
    assert not (env / "uploads" / ".m.ipynb.files").exists() and not (env / "uploads" / "m.ipynb").exists()


def test_replacing_renaming_copying_and_deleting_a_notebook_handle_its_files(env):
    _import(_zip([("m.ipynb", _nb()), ("weights.json", '{"w": 1}')]))
    # re-import with overwrite replaces the previous set
    _import(_zip([("m.ipynb", _nb()), ("other.json", "{}")]), overwrite=True)
    assert [f["path"] for f in client.get("/api/notebooks/m.ipynb/files").json()["files"]] == ["other.json"]
    # a plain re-upload of the notebook leaves its files alone
    client.post("/api/upload", params={"overwrite": "true"}, files={"file": ("m.ipynb", _nb(), "application/json")})
    assert client.get("/api/notebooks/m.ipynb/files").json()["total_count"] == 1
    # copy: its own copy; rename: moves; delete: removes
    assert client.post("/api/notebooks/m.ipynb/copy", json={"new_filename": "c.ipynb"}).status_code == 200
    assert client.get("/api/notebooks/c.ipynb/files").json()["total_count"] == 1
    assert client.patch("/api/notebooks/m.ipynb", json={"new_filename": "r.ipynb"}).status_code == 200
    assert client.get("/api/notebooks/r.ipynb/files").json()["total_count"] == 1 and not (env / "uploads" / ".m.ipynb.files").exists()
    assert client.delete("/api/notebooks/r.ipynb").status_code == 200
    assert not (env / "uploads" / ".r.ipynb.files").exists() and (env / "uploads" / ".c.ipynb.files").is_dir()


def test_exported_notebook_archives_do_not_turn_their_metadata_into_data_files(env):
    client.post("/api/upload", files={"file": ("m.ipynb", _nb(), "application/json")})
    client.put("/api/notebooks/m.ipynb/tags", json={"tags": ["x"]})
    exported = client.get("/api/notebooks/export")
    assert exported.status_code == 200
    client.delete("/api/notebooks/m.ipynb")
    results = _import(exported.content)
    assert results[0]["companion_files"] == [] and results[0]["companion_skipped"] == []


def _versioned(env, files_v1, files_v2):
    """A notebook imported twice (so its first state is a saved version), with data files per import."""
    def bundle(files):
        entries = [("m.ipynb", _nb())] + list(files.items())
        return _zip(entries)
    _import(bundle(files_v1))
    _import(bundle(files_v2), overwrite=True)
    versions = client.get("/api/notebooks/m.ipynb/versions").json()["versions"]
    return versions[0]["version_id"]


def test_a_versions_data_files_are_listed_with_sizes_and_hashes(env):
    import hashlib

    version = _versioned(env, {"weights.json": "AAAA", "models/vocab.txt": "x"}, {"weights.json": "BB"})
    body = client.get(f"/api/notebooks/m.ipynb/versions/{version}/files").json()
    assert body["recorded"] is True and body["total_count"] == 2 and body["total_bytes"] == 5
    assert {f["path"]: (f["bytes"], f["sha256"]) for f in body["files"]} == {
        "weights.json": (4, hashlib.sha256(b"AAAA").hexdigest()), "models/vocab.txt": (1, hashlib.sha256(b"x").hexdigest())}
    assert client.get("/api/notebooks/m.ipynb/versions/nope.ipynb/files").status_code == 404


def test_version_diff_reports_added_removed_and_changed_data_files(env):
    version = _versioned(env, {"weights.json": "AAAA", "gone.csv": "g", "same.txt": "s"},
                         {"weights.json": "BBBB", "new.csv": "n", "same.txt": "s"})
    files = client.get(f"/api/notebooks/m.ipynb/versions/{version}/diff").json()["files"]
    assert files["comparable"] is True
    assert [f["path"] for f in files["added"]] == ["new.csv"]
    assert [f["path"] for f in files["removed"]] == ["gone.csv"]
    assert [(f["path"], f["from"]["bytes"], f["to"]["bytes"]) for f in files["changed"]] == [("weights.json", 4, 4)]
    assert files["changed"][0]["from"]["sha256"] != files["changed"][0]["to"]["sha256"]


def test_two_saved_versions_can_be_compared_against_each_other(env):
    _import(_zip([("m.ipynb", _nb()), ("weights.json", "ONE")]))
    _import(_zip([("m.ipynb", _nb()), ("weights.json", "TWO")]), overwrite=True)
    _import(_zip([("m.ipynb", _nb()), ("weights.json", "TWO"), ("extra.txt", "e")]), overwrite=True)
    ids = [v["version_id"] for v in client.get("/api/notebooks/m.ipynb/versions").json()["versions"]]
    assert len(ids) == 2
    newest, oldest = ids[0], ids[1]
    files = client.get(f"/api/notebooks/m.ipynb/versions/{oldest}/diff", params={"against": newest}).json()["files"]
    assert files["comparable"] is True and [f["path"] for f in files["changed"]] == ["weights.json"]


def test_versions_without_recorded_data_files_are_not_compared_and_not_reported_as_empty(env):
    import shutil

    version = _versioned(env, {"weights.json": "AAAA"}, {"weights.json": "BBBB"})
    for snapshot in Path(upload_routes._notebook_versions_dir("m.ipynb")).glob("*.files"):
        shutil.rmtree(snapshot)
    listed = client.get(f"/api/notebooks/m.ipynb/versions/{version}/files").json()
    assert listed["recorded"] is False and listed["files"] == []
    files = client.get(f"/api/notebooks/m.ipynb/versions/{version}/diff").json()["files"]
    assert files == {"comparable": False, "from_recorded": False, "to_recorded": True, "added": [], "removed": [], "changed": []}


def test_a_recorded_version_with_no_files_is_distinct_from_not_recorded(env):
    version = _versioned(env, {}, {"weights.json": "AAAA"})
    versions_dir = Path(upload_routes._notebook_versions_dir("m.ipynb"))
    (versions_dir / f"{version}.files").mkdir(exist_ok=True)
    listed = client.get(f"/api/notebooks/m.ipynb/versions/{version}/files").json()
    assert listed["recorded"] is True and listed["files"] == []
    files = client.get(f"/api/notebooks/m.ipynb/versions/{version}/diff").json()["files"]
    assert files["comparable"] is True and [f["path"] for f in files["added"]] == ["weights.json"]
