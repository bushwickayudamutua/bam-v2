"""Frontend wiring: the operator console is served as static files and the
API mount does not shadow existing routes."""

from __future__ import annotations


def test_root_redirects_to_app(client) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/app/"


def test_app_index_is_served(client) -> None:
    resp = client.get("/app/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "BAM Operator Console" in resp.text


def test_static_assets_are_served(client) -> None:
    for path in ("/app/styles.css", "/app/app.js", "/app/views/checkin.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_api_route_not_shadowed_by_mount(client) -> None:
    resp = client.get("/metrics/open-requests")
    assert resp.status_code == 200
