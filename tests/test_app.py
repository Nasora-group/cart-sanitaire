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
    export_response = client.get("/export.csv")
    assert export_response.status_code == 200
    assert b"updated_at" in export_response.data


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


def test_csrf_rejects_post_without_token(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    app_module.app.config['WTF_CSRF_ENABLED'] = True
    response = app_module.app.test_client().post('/login', data={'password': 'no-token'})
    assert response.status_code == 400



def test_postgres_mode_does_not_seed_source_dataset(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "DB_URL", "postgresql://not-a-real-connection")
    monkeypatch.setattr(
        app_module, "connect",
        lambda: (_ for _ in ()).throw(AssertionError("should not seed PostgreSQL")),
    )
    app_module.seed_source_dataset()


def test_full_export_parser_preserves_ids_and_nullable_coordinates(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    headers = ",".join(app_module.RESTORE_COLUMNS)
    content = (
        headers + "\\n"
        + '7,Hopital A,Hôpital,Dakar,Dakar,Dakar,source,12.5,18.5,,,À vérifier,note,2026-10-24T10:00:00\\n'
    )
    rows = app_module.parse_full_export(content)
    assert len(rows) == 1
    assert rows[0]["id"] == 7
    assert rows[0]["svg_x"] == 12.5
    assert rows[0]["latitude"] is None
    assert rows[0]["notes"] == "note"


def test_full_export_parser_rejects_duplicate_ids(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    headers = ",".join(app_module.RESTORE_COLUMNS)
    line = '7,Hopital A,Hôpital,Dakar,Dakar,Dakar,source,,,,,À vérifier,note,2026-10-24T10:00:00\\n'
    with pytest.raises(ValueError, match="dupliqué"):
        app_module.parse_full_export(headers + "\\n" + line + line)


def test_restore_export_route_requires_admin(tmp_path, monkeypatch):
    app_module = load_app(tmp_path, monkeypatch)
    response = app_module.app.test_client().get("/admin/restore-export")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_restore_export_route_requires_postgres_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    app_module = load_app(tmp_path, monkeypatch)
    client = app_module.app.test_client()
    client.post("/login", data={"password": "test-admin-password"})
    response = client.get("/admin/restore-export")
    assert response.status_code == 200
    assert "DATABASE_URL" in response.get_data(as_text=True)
