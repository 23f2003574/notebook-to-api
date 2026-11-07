import nbformat

from backend.compiler import compile_notebook

SOURCE = (
    'def boom(a: int) -> int:\n'
    '    raise RuntimeError("failed with key sk-live-SUPERSECRET123456")\n'
)


def test_cluster_manifest_turns_on_error_hiding_so_secrets_in_messages_stay_server_side(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    out = tmp_path / "out"
    compile_notebook(str(tmp_path / "nb.ipynb"), str(out))

    manifest = (out / "kubernetes.yaml").read_text()
    entry = manifest.split("name: NOTEBOOK_API_HIDE_ERROR_DETAILS")[1].split("- name:")[0]

    assert 'value: "true"' in entry
    assert "SUPERSECRET" not in manifest
