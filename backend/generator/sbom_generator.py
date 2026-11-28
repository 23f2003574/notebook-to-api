"""A CycloneDX 1.5 software bill of materials for a compiled app, derived from its hash-locked requirements.txt.

The SBOM lists exactly what `pip install --require-hashes -r requirements.txt` installs: every direct and
transitive package of the lock with its version, package URL and SHA-256 hashes. It is deterministic (no
timestamp, a serial number derived from the content, sorted components) so equivalent builds produce
identical bytes, and it holds no file paths or environment data. `lock_requirements_with_hashes` must have
run first; an unhashed or unpinned requirement is an error, never a silent omission."""
from __future__ import annotations

import json
import os
import re
import uuid

SBOM_FILENAME = "sbom.cdx.json"
_ENTRY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\;]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


class SBOMError(ValueError):
    """The requirements are not a complete hash lock, so no SBOM can be written for them."""


def parse_locked_requirements(text: str) -> list[tuple[str, str, list[str]]]:
    """[(name, version, [sha256, ...])] for every entry of a hash-locked requirements file."""
    entries: list[list] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENTRY.match(line)
        if match:
            entries.append([match.group(1).lower().replace("_", "-"), match.group(2), []])
        elif entries and line.startswith("--hash="):
            entries[-1][2].extend(_HASH.findall(line))
        else:
            raise SBOMError(f"requirements line is not a pinned, hashed entry: {line[:60]!r}")
        if match:
            entries[-1][2].extend(_HASH.findall(line))
    missing = [name for name, _version, hashes in entries if not hashes]
    if missing:
        raise SBOMError(f"no hash for: {', '.join(sorted(missing))} (lock the requirements with --require-hashes first)")
    if not entries:
        raise SBOMError("no locked requirements to describe")
    return [(name, version, sorted(set(hashes))) for name, version, hashes in entries]


SCHEMA = {  # the parts of CycloneDX 1.5 this document uses, with the same required fields and formats
    "type": "object",
    "required": ["bomFormat", "specVersion", "serialNumber", "version", "metadata", "components", "dependencies"],
    "properties": {
        "bomFormat": {"const": "CycloneDX"},
        "specVersion": {"const": "1.5"},
        "serialNumber": {"type": "string", "pattern": "^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"},
        "version": {"type": "integer", "minimum": 1},
        "metadata": {"type": "object", "required": ["component"]},
        "components": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["type", "bom-ref", "name", "version", "purl", "hashes"],
            "properties": {"type": {"const": "library"}, "hashes": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["alg", "content"],
                "properties": {"alg": {"const": "SHA-256"}, "content": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}}}}},
        "dependencies": {"type": "array", "items": {"type": "object", "required": ["ref"]}},
    },
}


def build_sbom(requirements_text: str, application: str) -> dict:
    entries = parse_locked_requirements(requirements_text)
    components = [{
        "type": "library", "bom-ref": f"pkg:pypi/{name}@{version}", "name": name, "version": version,
        "purl": f"pkg:pypi/{name}@{version}", "hashes": [{"alg": "SHA-256", "content": digest} for digest in hashes],
    } for name, version, hashes in sorted(entries)]
    refs = [component["bom-ref"] for component in components]
    app_ref = f"app:{application}"
    document = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {"type": "application", "bom-ref": app_ref, "name": application}},
        "components": components,
        # The lock does not record which packages are direct; the application depends on all of them.
        "dependencies": [{"ref": app_ref, "dependsOn": refs}] + [{"ref": ref, "dependsOn": []} for ref in refs],
    }
    digest = json.dumps(document, sort_keys=True).encode()
    document["serialNumber"] = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "notebook-to-api-sbom:" + digest.decode()))
    return document


def validate_sbom(document: dict) -> None:
    import jsonschema

    jsonschema.validate(document, SCHEMA)


def write_sbom(output_dir: str, application: str | None = None) -> str:
    """Write sbom.cdx.json beside output_dir/requirements.txt; returns its path."""
    with open(os.path.join(output_dir, "requirements.txt"), encoding="utf-8") as handle:
        text = handle.read()
    document = build_sbom(text, application or os.path.basename(os.path.abspath(output_dir)))
    validate_sbom(document)
    path = os.path.join(output_dir, SBOM_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path
