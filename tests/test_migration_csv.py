import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_export_to_postgres.py"
SPEC = importlib.util.spec_from_file_location("healthmap_csv_migration", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)

HEADERS = ",".join(migration.COLUMNS)


def test_reads_full_export_and_preserves_ids_and_coordinates(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(
        HEADERS + "\n"
        + '7,Hopital A,Hôpital,Dakar,Dakar,Dakar,Fichier DTGC,12.5,18.5,14.7,-17.4,Vérifié,note,2026-10-24T10:00:00\n',
        encoding="utf-8",
    )
    rows = migration.read_export(path)
    assert len(rows) == 1
    assert rows[0]["id"] == 7
    assert rows[0]["svg_x"] == 12.5
    assert rows[0]["latitude"] == 14.7
    assert rows[0]["notes"] == "note"


def test_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "export.csv"
    line = '7,Hopital A,Hôpital,Dakar,Dakar,Dakar,source,,,,,À vérifier,,2026-10-24T10:00:00\n'
    path.write_text(HEADERS + "\n" + line + line, encoding="utf-8")
    with pytest.raises(ValueError, match="dupliqué"):
        migration.read_export(path)


def test_rejects_missing_export_columns(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("id,name\n1,Hopital A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Colonnes manquantes"):
        migration.read_export(path)


def test_rejects_empty_export(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(HEADERS + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aucune fiche"):
        migration.read_export(path)
