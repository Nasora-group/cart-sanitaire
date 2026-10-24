#!/usr/bin/env python3
"""Safely restore a HealthMap CSV export into an empty PostgreSQL facilities table.

Dry-run by default. The source must be the full CSV produced by /export.csv.
This script intentionally refuses to merge into a non-empty target.
"""
import argparse
import csv
import os
import sys
from pathlib import Path

COLUMNS = (
    "id", "name", "facility_type", "region", "department", "commune",
    "source", "svg_x", "svg_y", "latitude", "longitude",
    "verification_status", "notes", "updated_at",
)
NUMERIC_COLUMNS = {"svg_x", "svg_y", "latitude", "longitude"}
REQUIRED_COLUMNS = set(COLUMNS)


def read_export(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError("Colonnes manquantes dans le CSV : " + ", ".join(sorted(missing)))
        rows = []
        seen_ids = set()
        for line_number, raw in enumerate(reader, start=2):
            row = {column: (raw.get(column) or "").strip() for column in COLUMNS}
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
            for column in NUMERIC_COLUMNS:
                if row[column] == "":
                    row[column] = None
                else:
                    try:
                        row[column] = float(row[column])
                    except ValueError:
                        raise ValueError(f"Valeur numérique invalide ({column}) à la ligne {line_number}.") from None
            rows.append(row)
    if not rows:
        raise ValueError("Le CSV ne contient aucune fiche à migrer.")
    return rows


def migrate(rows, database_url):
    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM facilities")
            existing = cursor.fetchone()[0]
            if existing:
                raise RuntimeError(
                    f"La base cible contient déjà {existing} fiche(s). Migration annulée pour éviter les doublons."
                )
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            columns_sql = ", ".join(COLUMNS)
            query = f"INSERT INTO facilities ({columns_sql}) VALUES ({placeholders})"
            for row in rows:
                cursor.execute(query, tuple(row[column] for column in COLUMNS))
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('facilities', 'id'), "
                "COALESCE((SELECT MAX(id) FROM facilities), 1), true)"
            )
        # psycopg commits on successful context exit; exceptions roll back the transaction.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", help="Chemin du CSV complet exporté depuis HealthMap")
    parser.add_argument("--apply", action="store_true", help="Écrire réellement dans PostgreSQL (sinon simulation)")
    args = parser.parse_args()

    try:
        rows = read_export(args.csv_file)
        print(f"Contrôle réussi : {len(rows)} fiches, {len(COLUMNS)} colonnes attendues.")
        if not args.apply:
            print("Simulation uniquement. Vérifiez la sauvegarde et la base cible, puis relancez avec --apply.")
            return 0
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            print("Erreur : la variable DATABASE_URL n'est pas définie.", file=sys.stderr)
            return 2
        migrate(rows, database_url)
        print(f"Migration terminée : {len(rows)} fiches copiées avec leurs identifiants.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Migration annulée : {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
