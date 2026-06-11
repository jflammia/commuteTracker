import dataclasses

from fastapi.testclient import TestClient

from backend.app import create_app


def _app_with_build(settings, tmp_path):
    build = tmp_path / "fake_build"
    build.mkdir()
    (build / "index.html").write_text("<html><body>SPA</body></html>")
    (build / "app.js").write_text("console.log('hi')")
    s = dataclasses.replace(settings, frontend_build_dir=build)
    return create_app(s)


def test_serves_static_file(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_deep_link_falls_back_to_index(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/trips/t12345")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_api_routes_still_win(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/trips")
    assert resp.status_code == 200
    assert resp.json() == []


def test_no_build_dir_no_spa(settings):
    app = create_app(settings)  # default: no frontend build
    with TestClient(app) as c:
        assert c.get("/trips/t1").status_code == 404


def test_spa_blocks_path_traversal(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    secret = tmp_path / "secret.txt"  # sibling of the fake build dir
    secret.write_text("TOP SECRET")
    with TestClient(app) as c:
        # encoded ../ — Starlette decodes %2e%2e%2f to ../ before routing
        resp = c.get("/%2e%2e%2fsecret.txt")
    # must NOT serve the secret; falls back to the SPA index instead
    assert "TOP SECRET" not in resp.text
    assert "SPA" in resp.text  # index.html fallback content from _app_with_build
