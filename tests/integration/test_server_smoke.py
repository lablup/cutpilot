"""Smoke test for `cutpilot.server` — the FastAPI facade over `run_pipeline`.

Uses FastAPI's TestClient so we don't need a real uvicorn process. Doesn't
submit a real pipeline run (that would need live NIMs + many minutes); only
exercises the routing surface.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cutpilot.server import app

pytestmark = [pytest.mark.integration]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_routes_declared(client: TestClient) -> None:
    """Every route we document in the module docstring is registered."""
    paths = {r.path for r in app.routes}
    # Declared endpoints per server.py docstring
    assert "/runs" in paths
    # Static mounts show up with a trailing segment
    mount_paths = {getattr(r, "path", None) for r in app.routes}
    assert any(p and ("/outputs" in p or p == "/") for p in mount_paths)


def test_create_run_rejects_empty_source(client: TestClient) -> None:
    """`CreateRunRequest.source` has `min_length=1` — empty string must 422."""
    r = client.post("/runs", json={"source": ""})
    assert r.status_code == 422


def test_get_unknown_run_404(client: TestClient) -> None:
    r = client.get("/runs/not-a-real-run-id")
    assert r.status_code == 404
