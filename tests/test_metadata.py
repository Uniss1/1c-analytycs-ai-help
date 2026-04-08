"""Tests for metadata register lookup."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from api.metadata import init_metadata, find_register, get_all_registers, get_dashboard_registers


@pytest.fixture()
def db_path():
    """Create a temp metadata.db with test data."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metadata.db"
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")

        from scripts.seed_metadata import create_schema, seed_data
        create_schema(cur)
        seed_data(cur)
        conn.commit()
        conn.close()

        init_metadata(str(path))
        yield path


def test_find_register_by_keyword(db_path):
    result = find_register("какая выручка за март?")
    assert result is not None
    assert result["name"] == "РегистрНакопления.ВитринаВыручка"
    assert "dimensions" in result
    assert "resources" in result
    assert result["register_type"] == "accumulation_turnover"


def test_find_register_with_dashboard_context(db_path):
    result = find_register("численность персонала", dashboard_context={"slug": "costs"})
    assert result is not None
    assert result["name"] == "РегистрНакопления.ВитринаПерсонал"


def test_find_register_not_found(db_path):
    result = find_register("какая погода завтра?")
    assert result is None


def test_get_dashboard_registers(db_path):
    regs = get_dashboard_registers("sales")
    names = [r["name"] for r in regs]
    assert "РегистрНакопления.ВитринаВыручка" in names
    assert "РегистрНакопления.ВитринаПерсонал" in names
    assert len(names) == 2


def test_get_all_registers(db_path):
    regs = get_all_registers()
    assert len(regs) == 3
