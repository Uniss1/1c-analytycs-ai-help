"""Tests for seed_metadata — schema and seeding with new annotation columns."""

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from scripts.seed_metadata import create_schema, seed_from_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_YAML = REPO_ROOT / "registers.example.yaml"

_TYPE_PREFIXES = (
    "РегистрСведений.",
    "РегистрНакопления.",
    "РегистрБухгалтерии.",
    "РегистрРасчёта.",
    "РегистрРасчета.",
)


def test_example_yaml_register_names_have_no_type_prefix():
    """1С expects bare register identifiers in the payload — type comes from
    the `type` field, not the `name`. Prefix in `name` breaks routing in BSL.
    Pin this in the example template so future edits don't regress it."""
    data = yaml.safe_load(EXAMPLE_YAML.read_text(encoding="utf-8"))
    for reg in data.get("registers", []):
        name = reg.get("name", "")
        for prefix in _TYPE_PREFIXES:
            assert not name.startswith(prefix), (
                f"registers.example.yaml: register {name!r} must be a bare "
                f"identifier (drop the {prefix!r} prefix); type lives in `type`."
            )


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    create_schema(cur)
    conn.commit()
    yield conn
    conn.close()


def test_dimensions_table_has_annotation_columns(db):
    """New columns technical, role, description_en exist in dimensions table."""
    row = db.execute("PRAGMA table_info(dimensions)").fetchall()
    col_names = [r["name"] for r in row]
    assert "technical" in col_names
    assert "role" in col_names
    assert "description_en" in col_names


def test_seed_preserves_annotations(db):
    """Seeding from YAML preserves technical/role/description_en."""
    data = {
        "registers": [{
            "name": "Тест.Регистр",
            "description": "Test",
            "type": "information_register",
            "dimensions": [
                {
                    "name": "ДЗО",
                    "data_type": "Строка",
                    "required": True,
                    "technical": False,
                    "role": "both",
                    "description_en": "company / subsidiary",
                    "values": ["А", "Б"],
                },
                {
                    "name": "Масштаб",
                    "data_type": "Строка",
                    "required": False,
                    "technical": True,
                },
            ],
            "resources": [{"name": "Сумма"}],
        }],
        "dashboards": [],
    }
    seed_from_yaml(db.cursor(), data)
    db.commit()

    dims = db.execute(
        "SELECT name, technical, role, description_en FROM dimensions ORDER BY name"
    ).fetchall()
    assert len(dims) == 2

    dzo = next(d for d in dims if d["name"] == "ДЗО")
    assert dzo["technical"] == 0
    assert dzo["role"] == "both"
    assert dzo["description_en"] == "company / subsidiary"

    scale = next(d for d in dims if d["name"] == "Масштаб")
    assert scale["technical"] == 1
    assert scale["role"] == "filter"  # default
    assert scale["description_en"] is None
