from fastapi.testclient import TestClient

from backend import dashboard
from backend.routes import upload as upload_module


def test_storage_report_includes_free_disk_and_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(upload_module, "MIN_FREE_DISK_BYTES", 12345)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", raising=False)
    data = TestClient(dashboard.app).get("/api/notebooks/storage?limit=1").json()
    assert data["min_free_disk_bytes"] == 12345
    assert isinstance(data["free_disk_bytes"], int) and data["free_disk_bytes"] > 0
