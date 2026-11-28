"""A CycloneDX SBOM next to each hash-locked app's requirements."""
import hashlib
import json

import pytest

from backend.compiler import lock_requirements_with_hashes
from backend.generator import sbom_generator as sbom

A, B = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
LOCK = f"""# Hash-locked
beta==2.0 \\
    --hash=sha256:{B}
alpha==1.0 \\
    --hash=sha256:{A} \\
    --hash=sha256:{B}
"""


def resolver(requirements):
    return [("alpha", "1.0", A), ("alpha", "1.0", B), ("beta", "2.0", B)]


def test_every_locked_package_and_hash_is_in_a_schema_valid_document():
    document = sbom.build_sbom(LOCK, "demo")
    sbom.validate_sbom(document)
    assert [(c["name"], c["version"]) for c in document["components"]] == [("alpha", "1.0"), ("beta", "2.0")]
    assert [h["content"] for h in document["components"][0]["hashes"]] == sorted({A, B})
    assert document["components"][1]["purl"] == "pkg:pypi/beta@2.0"
    root = document["dependencies"][0]
    assert root["ref"] == "app:demo" and root["dependsOn"] == ["pkg:pypi/alpha@1.0", "pkg:pypi/beta@2.0"]


def test_incomplete_or_unpinned_dependency_data_is_rejected():
    with pytest.raises(sbom.SBOMError, match="no hash"):
        sbom.build_sbom("alpha==1.0\n", "demo")
    with pytest.raises(sbom.SBOMError, match="not a pinned"):
        sbom.build_sbom(f"alpha>=1.0 --hash=sha256:{A}\n", "demo")
    with pytest.raises(sbom.SBOMError, match="no locked"):
        sbom.build_sbom("# nothing\n", "demo")
    bad = sbom.build_sbom(LOCK, "demo")
    bad["components"][0]["hashes"][0]["content"] = "xyz"
    with pytest.raises(Exception):  # noqa: B017 - jsonschema.ValidationError
        sbom.validate_sbom(bad)


def test_equivalent_builds_are_byte_identical_and_contain_no_local_data(tmp_path):
    outputs = []
    for name in ("one", "two"):
        directory = tmp_path / name / "app"
        directory.mkdir(parents=True)
        (directory / "requirements.txt").write_text("alpha==1.0\nbeta==2.0\n")
        lock_requirements_with_hashes(str(directory / "requirements.txt"), resolver)
        outputs.append(open(sbom.write_sbom(str(directory))).read())
    assert outputs[0] == outputs[1]
    assert str(tmp_path) not in outputs[0] and json.loads(outputs[0])["serialNumber"].startswith("urn:uuid:")
    other = sbom.build_sbom(LOCK.replace("2.0", "2.1"), "app")
    assert other["serialNumber"] != json.loads(outputs[0])["serialNumber"]


def test_a_requirements_file_that_is_not_locked_writes_nothing(tmp_path):
    (tmp_path / "requirements.txt").write_text("alpha==1.0\n")
    with pytest.raises(sbom.SBOMError):
        sbom.write_sbom(str(tmp_path))
    assert not (tmp_path / sbom.SBOM_FILENAME).exists()


def test_the_compile_command_writes_it_after_hash_locking():
    source = open("backend/cli.py").read()
    assert source.index("lock_requirements_with_hashes(") < source.index("write_sbom(str(output_dir))")
    assert "sbom.cdx.json" in open("README.md").read()
