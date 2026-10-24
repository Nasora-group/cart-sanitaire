import importlib
import sys


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "healthmap-test.db"))
    sys.modules.pop("app", None)
    import app
    return importlib.reload(app)


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
