import importlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "healthmap-test.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app", None)
    import app
    app = importlib.reload(app)
    app.app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_health_endpoint(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    client = app_module.app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_source_dataset_is_seeded_once(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    with app_module.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0] == 2568
        assert con.execute("SELECT COUNT(*) FROM facilities WHERE svg_x IS NOT NULL").fetchone()[0] > 0
        assert con.execute("SELECT COUNT(*) FROM facilities WHERE latitude IS NOT NULL").fetchone()[0] == 0
    app_module.seed_source_dataset()
    with app_module.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0] == 2568


def test_homepage_and_csv_export(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    client = app_module.app.test_client()
    home = client.get("/")
    assert home.status_code == 200
    assert b"Carte interactive" in home.data
    assert b"2 568" in home.data or b"2,568" in home.data
    assert client.get("/export.csv").status_code == 200


def test_admin_requires_login(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    client = app_module.app.test_client()
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_with_admin_password(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    app_module = load_app(tmp_path, monkeypatch)
    client = app_module.app.test_client()
    response = client.post("/login", data={"password": "test-admin-password"})
    assert response.status_code == 302
    assert client.get("/admin").status_code == 200


def test_csrf_token_is_present_on_login(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    response = app_module.app.test_client().get('/login')
    assert response.status_code == 200
    assert b'name="csrf_token"' in response.data
