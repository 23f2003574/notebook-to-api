"""Filtered, cursor-paged governance history."""
import threading

import pytest
from fastapi.testclient import TestClient

from backend.dashboard import app
from backend.observability import deployment_governance_audit as audit

client = TestClient(app)


@pytest.fixture(params=["sqlite", "memory"])
def trail(request, tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", request.param)
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "h.db"))
    audit.reset_audit_service()
    yield audit.get_audit_service()
    audit.reset_audit_service()


def _add(svc, action, notebook, outcome="success", n=1):
    for _ in range(n):
        svc.record(action=action, actor="dashboard", resource=f"notebook:{notebook}", outcome=outcome)


def _page(**params):
    r = client.get("/governance/audit/history", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_filters_compose(trail):
    _add(trail, "notebook.compile", "a.ipynb", n=2)
    _add(trail, "notebook.compile", "a.ipynb", "failure")
    _add(trail, "notebook.deploy", "a.ipynb", "failure")
    _add(trail, "notebook.compile", "b.ipynb")
    trail.record(action="route_change", actor="x", resource="r", outcome="success")
    assert len(_page(scope="notebook")["records"]) == 5
    assert len(_page(notebook="a.ipynb")["records"]) == 4
    assert len(_page(notebook="a.ipynb", action="notebook.compile")["records"]) == 3
    only = _page(notebook="a.ipynb", action="notebook.compile", outcome="failure")["records"]
    assert [r["outcome"] for r in only] == ["failure"]
    assert _page(notebook="nobody.ipynb")["records"] == []


def test_pagination_is_newest_first_without_gaps_or_repeats(trail):
    _add(trail, "notebook.compile", "p.ipynb", n=23)
    seen, cursor = [], None
    while True:
        page = _page(notebook="p.ipynb", limit=10, **({"cursor": cursor} if cursor else {}))
        seen += [r["sequence"] for r in page["records"]]
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert seen == sorted(seen, reverse=True) and len(seen) == len(set(seen)) == 23


def test_records_created_between_pages_do_not_shift_the_next_page(trail):
    _add(trail, "notebook.compile", "c.ipynb", n=6)
    first = _page(notebook="c.ipynb", limit=3)
    _add(trail, "notebook.compile", "c.ipynb", n=4)  # arrives while the user is paging
    second = _page(notebook="c.ipynb", limit=3, cursor=first["next_cursor"])
    got = [r["sequence"] for r in first["records"]] + [r["sequence"] for r in second["records"]]
    assert got == sorted(got, reverse=True) and len(set(got)) == 6 and max(got) == first["records"][0]["sequence"]


def test_concurrent_writers_during_reads_never_break_a_page(trail):
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            _add(trail, "notebook.deploy", "w.ipynb")

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(20):
            records = _page(notebook="w.ipynb", limit=5)["records"]
            seqs = [r["sequence"] for r in records]
            assert seqs == sorted(seqs, reverse=True)
    finally:
        stop.set()
        t.join()


def test_invalid_cursors_and_parameters_are_rejected(trail):
    for bad in ("not-a-cursor", "c2VxOi0z", "c2VxOmFiYw", "%%%"):
        assert client.get("/governance/audit/history", params={"cursor": bad}).status_code == 400
    assert client.get("/governance/audit/history", params={"outcome": "maybe"}).status_code == 422
    assert client.get("/governance/audit/history", params={"limit": 0}).status_code == 422
    assert client.get("/governance/audit/history", params={"limit": 100000}).status_code == 422


def test_a_large_history_is_bounded_per_request(trail):
    _add(trail, "notebook.compile", "big.ipynb", n=300)
    page = _page(notebook="big.ipynb", limit=200)
    assert len(page["records"]) == 200 and page["has_more"] is True
    assert len(_page(notebook="big.ipynb")["records"]) == 25  # default page size


def test_like_wildcards_in_filters_are_literal(trail):
    _add(trail, "notebook.compile", "100%.ipynb")
    _add(trail, "notebook.compile", "other.ipynb")
    assert len(_page(notebook="100%.ipynb")["records"]) == 1
