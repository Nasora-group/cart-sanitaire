import csv
import io
import os
import re
import json
import hmac
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_wtf.csrf import CSRFProtect

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(DATA_DIR, "healthmap.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-before-production")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    WTF_CSRF_TIME_LIMIT=3600,
)
csrf = CSRFProtect(app)

class HybridRow:
    """Row compatible with sqlite3.Row: access by column name or index."""
    def __init__(self, columns, values):
        self._columns = columns
        self._values = values
        self._data = dict(zip(columns, values))
    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._data[key]
    def __iter__(self):
        return iter(self._values)
    def keys(self):
        return self._columns
    def __len__(self):
        return len(self._values)

def pg_row_factory(cursor):
    columns = [col.name for col in cursor.description]
    return lambda values: HybridRow(columns, values)

class PostgresConnection:
    def __init__(self, raw):
        self.raw = raw
    def execute(self, sql, params=()):
        # The app uses SQLite-style positional placeholders; translate for psycopg.
        return self.raw.execute(sql.replace("?", "%s"), params)
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.raw.rollback()
            else:
                self.raw.commit()
        finally:
            self.raw.close()
        return False

@app.before_request
def protect_admin_routes():
    if request.path.startswith(("/admin", "/import")) and not session.get("healthmap_admin"):
        return redirect(url_for("login", next=request.path))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        expected = os.environ.get("ADMIN_PASSWORD", "")
        supplied = request.form.get("password", "")
        if expected and hmac.compare_digest(supplied, expected):
            session["healthmap_admin"] = True
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("admin")
            return redirect(destination)
        flash("Mot de passe incorrect ou accès administrateur non configuré.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))

def connect():
    if DB_URL:
        import psycopg
        url = DB_URL.replace("postgres://", "postgresql://", 1)
        raw = psycopg.connect(url, row_factory=pg_row_factory)
        return PostgresConnection(raw)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        if DB_URL:
            con.execute("""CREATE TABLE IF NOT EXISTS facilities (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                facility_type TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
                commune TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                svg_x DOUBLE PRECISION, svg_y DOUBLE PRECISION,
                latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
                verification_status TEXT NOT NULL DEFAULT 'À vérifier',
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        else:
            con.execute("""CREATE TABLE IF NOT EXISTS facilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                facility_type TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
                commune TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                svg_x REAL, svg_y REAL, latitude REAL, longitude REAL,
                verification_status TEXT NOT NULL DEFAULT 'À vérifier',
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_facilities_region ON facilities(region)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_facilities_commune ON facilities(commune)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_facilities_type ON facilities(facility_type)")


def seed_source_dataset():
    """Initialise le répertoire depuis les points intégrés à la carte originale."""
    # En mode PostgreSQL, ne jamais semer les données d'origine au démarrage :
    # la restauration contrôlée doit importer l'export complet avec ses IDs.
    if DB_URL:
        return
    map_path = os.path.join(ROOT, "static", "carte-sanitaire-originale.html")
    if not os.path.exists(map_path):
        return
    with connect() as con:
        if con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]:
            return
        with open(map_path, "r", encoding="utf-8") as source:
            html = source.read()
        match = re.search(r"const D=(\{.*?\});", html, re.S)
        if not match:
            app.logger.error("Données cartographiques introuvables dans la carte source.")
            return
        dataset = json.loads(match.group(1))
        communes = dataset.get("communes", [])
        regions = dataset.get("regions", {})
        departments = dict(dataset.get("deps", []))
        source_labels = {0: "Fichier DTGC", 1: "OpenStreetMap (octobre 2026)"}
        for index, point in enumerate(dataset.get("pts", []), start=1):
            name, facility_type, svg_x, svg_y, commune_index, source_code = (point + [None] * 6)[:6]
            commune = communes[commune_index] if isinstance(commune_index, int) and 0 <= commune_index < len(communes) else {}
            region_code = commune.get("reg", "")
            department_code = commune.get("dep", "")
            con.execute(
                """INSERT INTO facilities
                (name, facility_type, region, department, commune, source,
                 svg_x, svg_y, verification_status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name or "", facility_type or "",
                    regions.get(region_code, {}).get("name", ""),
                    departments.get(department_code, ""),
                    commune.get("n", ""),
                    source_labels.get(source_code, "Source non précisée"),
                    float(svg_x) if svg_x is not None else None,
                    float(svg_y) if svg_y is not None else None,
                    "À vérifier",
                    f"Point source #{index}; coordonnées SVG uniquement; localisation GPS non validée",
                ),
            )

def distinct_values(con, column):
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT {column} FROM facilities WHERE {column} <> '' ORDER BY {column}"
    ).fetchall()]

@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    selected = {k: request.args.get(k, "").strip() for k in ("region", "department", "commune", "type", "status")}
    where, params = [], []
    if q:
        where.append("(name LIKE ? OR commune LIKE ? OR department LIKE ? OR region LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    for column, key in (("region", "region"), ("department", "department"),
                        ("commune", "commune"), ("facility_type", "type"),
                        ("verification_status", "status")):
        if selected[key]:
            where.append(f"{column} = ?")
            params.append(selected[key])
    clause = " WHERE " + " AND ".join(where) if where else ""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50
    with connect() as con:
        total = con.execute("SELECT COUNT(*) FROM facilities" + clause, params).fetchone()[0]
        rows = con.execute("SELECT * FROM facilities" + clause +
            " ORDER BY region, department, commune, name LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page]).fetchall()
        stats = {
            "total": con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0],
            "regions": con.execute("SELECT COUNT(DISTINCT region) FROM facilities WHERE region <> ''").fetchone()[0],
            "communes": con.execute("SELECT COUNT(DISTINCT commune) FROM facilities WHERE commune <> ''").fetchone()[0],
            "review": con.execute("SELECT COUNT(*) FROM facilities WHERE verification_status <> 'Vérifié' OR name = '' OR facility_type = ''").fetchone()[0]
        }
        filters = {
            "regions": distinct_values(con, "region"),
            "departments": distinct_values(con, "department"),
            "communes": distinct_values(con, "commune"),
            "types": distinct_values(con, "facility_type"),
            "statuses": distinct_values(con, "verification_status")
        }
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("index.html", rows=rows, stats=stats, filters=filters,
        q=q, selected=selected, total=total, page=page, pages=pages)

@app.route("/admin")
def admin():
    with connect() as con:
        rows = con.execute("""SELECT id,name,facility_type,region,department,commune,verification_status
            FROM facilities ORDER BY CASE WHEN verification_status='Vérifié' THEN 1 ELSE 0 END, region, name
            LIMIT 300""").fetchall()
    return render_template("admin.html", rows=rows)

@app.route("/admin/facility/<int:facility_id>", methods=["GET", "POST"])
def edit_facility(facility_id):
    fields = ["name", "facility_type", "region", "department", "commune", "source", "verification_status", "notes"]
    with connect() as con:
        row = con.execute("SELECT * FROM facilities WHERE id=?", (facility_id,)).fetchone()
        if row is None:
            flash("Fiche introuvable.", "error")
            return redirect(url_for("admin"))
        if request.method == "POST":
            values = [request.form.get(field, "").strip() for field in fields]
            values.extend([datetime.utcnow().isoformat(timespec="seconds"), facility_id])
            con.execute("""UPDATE facilities SET name=?,facility_type=?,region=?,department=?,commune=?,
                source=?,verification_status=?,notes=?,updated_at=? WHERE id=?""", values)
            flash("Fiche enregistrée.", "success")
            return redirect(url_for("edit_facility", facility_id=facility_id))
    return render_template("edit.html", row=row)

@app.route("/import", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Choisissez un fichier CSV.", "error")
            return redirect(url_for("import_csv"))
        try:
            text = upload.stream.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            normalized = {str(k).strip().lower(): k for k in (reader.fieldnames or [])}
            def value(row, *names):
                for name in names:
                    original = normalized.get(name)
                    if original and row.get(original) is not None:
                        return str(row[original]).strip()
                return ""
            count = 0
            with connect() as con:
                for row in reader:
                    con.execute("""INSERT INTO facilities
                        (name,facility_type,region,department,commune,source,verification_status)
                        VALUES (?,?,?,?,?,?,?)""", (
                        value(row, "name", "nom", "etablissement"),
                        value(row, "facility_type", "type", "type_structure", "categorie"),
                        value(row, "region"), value(row, "department", "departement"),
                        value(row, "commune"), value(row, "source"),
                        value(row, "verification_status", "statut_verification") or "À vérifier"))
                    count += 1
            flash(f"Import terminé : {count} ligne(s) ajoutée(s).", "success")
        except Exception:
            app.logger.exception("Échec de l’import CSV")
            flash("Import impossible. Vérifiez le fichier CSV et réessayez.", "error")
        return redirect(url_for("admin"))
    return render_template("import.html")

RESTORE_COLUMNS = (
    "id", "name", "facility_type", "region", "department", "commune",
    "source", "svg_x", "svg_y", "latitude", "longitude",
    "verification_status", "notes", "updated_at",
)
RESTORE_NUMERIC_COLUMNS = {"svg_x", "svg_y", "latitude", "longitude"}


def parse_full_export(text):
    """Valide un export complet sans modifier la base."""
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    missing = set(RESTORE_COLUMNS) - headers
    if missing:
        raise ValueError("Colonnes manquantes : " + ", ".join(sorted(missing)))
    rows, seen_ids = [], set()
    for line_number, raw in enumerate(reader, start=2):
        row = {column: (raw.get(column) or "").strip() for column in RESTORE_COLUMNS}
        try:
            row["id"] = int(row["id"])
        except (TypeError, ValueError):
            raise ValueError(f"Identifiant invalide à la ligne {line_number}.") from None
        if row["id"] <= 0:
            raise ValueError(f"Identifiant non positif à la ligne {line_number}.")
        if row["id"] in seen_ids:
            raise ValueError(f"Identifiant dupliqué ({row['id']}) à la ligne {line_number}.")
        seen_ids.add(row["id"])
        if not row["updated_at"]:
            raise ValueError(f"Date de mise à jour manquante à la ligne {line_number}.")
        for column in RESTORE_NUMERIC_COLUMNS:
            if row[column] == "":
                row[column] = None
            else:
                try:
                    row[column] = float(row[column])
                except ValueError:
                    raise ValueError(f"Valeur numérique invalide ({column}) à la ligne {line_number}.") from None
        rows.append(row)
    if not rows:
        raise ValueError("Le CSV ne contient aucune fiche à restaurer.")
    return rows


@app.route("/admin/restore-export", methods=["GET", "POST"])
def restore_export():
    if not DB_URL:
        flash("Restauration désactivée : configurez DATABASE_URL vers la base PostgreSQL cible dans Render.", "error")
        return render_template("restore_export.html", postgres_ready=False)
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Choisissez le fichier CSV complet exporté depuis HealthMap.", "error")
            return redirect(url_for("restore_export"))
        try:
            content = upload.stream.read().decode("utf-8-sig")
            rows = parse_full_export(content)
            with connect() as con:
                existing = con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
                if existing:
                    raise ValueError(
                        f"La base cible contient déjà {existing} fiche(s). Aucun changement effectué."
                    )
                placeholders = ", ".join(["?"] * len(RESTORE_COLUMNS))
                columns_sql = ", ".join(RESTORE_COLUMNS)
                sql = f"INSERT INTO facilities ({columns_sql}) VALUES ({placeholders})"
                for row in rows:
                    con.execute(sql, [row[column] for column in RESTORE_COLUMNS])
                con.execute(
                    "SELECT setval(pg_get_serial_sequence('facilities', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM facilities), 1), true)"
                )
            flash(f"Restauration terminée : {len(rows)} fiches importées avec leurs identifiants et dates.", "success")
            return redirect(url_for("admin"))
        except ValueError as error:
            flash(str(error), "error")
        except Exception:
            app.logger.exception("Échec de la restauration complète PostgreSQL")
            flash("Restauration annulée. Vérifiez la connexion PostgreSQL et les données ; aucune validation partielle n'est annoncée.", "error")
    return render_template("restore_export.html", postgres_ready=True)


@app.route("/export.csv")
def export_csv():
    filters = {k: request.args.get(k, "").strip() for k in ("q", "region", "department", "commune", "type")}
    where, params = [], []
    if filters["q"]:
        where.append("(name LIKE ? OR commune LIKE ? OR department LIKE ? OR region LIKE ?)")
        params.extend([f"%{filters['q']}%"] * 4)
    for column, key in (("region", "region"), ("department", "department"), ("commune", "commune"), ("facility_type", "type")):
        if filters[key]:
            where.append(f"{column} = ?")
            params.append(filters[key])
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect() as con:
        rows = con.execute("SELECT id,name,facility_type,region,department,commune,source,svg_x,svg_y,latitude,longitude,verification_status,notes,updated_at FROM facilities" + clause + " ORDER BY region,department,commune,name", params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0].keys() if rows else ["id","name","facility_type","region","department","commune","source","svg_x","svg_y","latitude","longitude","verification_status","notes","updated_at"])
    writer.writerows([tuple(r) for r in rows])
    stream = io.BytesIO(("\ufeff" + output.getvalue()).encode("utf-8"))
    return send_file(stream, mimetype="text/csv", as_attachment=True, download_name="healthmap-etablissements.csv")

@app.route("/health")
def health():
    return {"status": "ok"}

init_db()
seed_source_dataset()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
