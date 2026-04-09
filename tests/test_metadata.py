"""Tests for metadata register lookup."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from api.metadata import init_metadata, find_register, get_all_registers, get_dashboard_registers


@pytest.fixture()
def db_path():
    """Create a temp metadata.db with inline test data (not from YAML)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metadata.db"
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")

        from scripts.seed_metadata import create_schema
        create_schema(cur)

        # Inline test data
        cur.executescript("""
            INSERT INTO dashboards (id, slug, title, url_pattern) VALUES (1, 'sales', 'Продажи', '/analytics/sales*');
            INSERT INTO dashboards (id, slug, title, url_pattern) VALUES (2, 'costs', 'Затраты', '/analytics/costs*');

            INSERT INTO registers (id, name, description, register_type) VALUES (1, 'РегистрНакопления.ВитринаВыручка', 'Выручка', 'accumulation_turnover');
            INSERT INTO registers (id, name, description, register_type) VALUES (2, 'РегистрНакопления.ВитринаЗатрат', 'Затраты', 'accumulation_turnover');
            INSERT INTO registers (id, name, description, register_type) VALUES (3, 'РегистрНакопления.ВитринаПерсонал', 'Персонал', 'accumulation_turnover');

            INSERT INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (1, 1, 'Выручка');
            INSERT INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (1, 3, 'Численность');
            INSERT INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (2, 2, 'Затраты');
            INSERT INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (2, 3, 'ФОТ');

            INSERT INTO dimensions (register_id, name, data_type) VALUES (1, 'Период', 'Дата');
            INSERT INTO dimensions (register_id, name, data_type) VALUES (1, 'Подразделение', 'Строка');
            INSERT INTO resources (register_id, name, data_type) VALUES (1, 'Сумма', 'Число');

            INSERT INTO dimensions (register_id, name, data_type) VALUES (2, 'Период', 'Дата');
            INSERT INTO resources (register_id, name, data_type) VALUES (2, 'Сумма', 'Число');

            INSERT INTO dimensions (register_id, name, data_type) VALUES (3, 'Период', 'Дата');
            INSERT INTO resources (register_id, name, data_type) VALUES (3, 'Численность', 'Число');

            INSERT INTO keywords (register_id, keyword) VALUES (1, 'выручка');
            INSERT INTO keywords (register_id, keyword) VALUES (1, 'продажи');
            INSERT INTO keywords (register_id, keyword) VALUES (2, 'затраты');
            INSERT INTO keywords (register_id, keyword) VALUES (2, 'расходы');
            INSERT INTO keywords (register_id, keyword) VALUES (3, 'персонал');
            INSERT INTO keywords (register_id, keyword) VALUES (3, 'численность');
        """)
        conn.commit()
        conn.close()

        init_metadata(str(path))
        yield path


def test_find_register_by_keyword(db_path):
    result, debug = find_register("какая выручка за март?")
    assert result is not None
    assert result["name"] == "РегистрНакопления.ВитринаВыручка"
    assert "dimensions" in result
    assert "resources" in result
    assert result["register_type"] == "accumulation_turnover"
    assert "выручка" in debug["matching_keywords"]


def test_find_register_with_dashboard_context(db_path):
    result, debug = find_register("численность персонала", dashboard_context={"slug": "costs"})
    assert result is not None
    assert result["name"] == "РегистрНакопления.ВитринаПерсонал"


def test_find_register_not_found(db_path):
    result, debug = find_register("какая погода завтра?")
    assert result is None
    assert debug["extracted_words"]  # words were extracted but no match


def test_get_dashboard_registers(db_path):
    regs = get_dashboard_registers("sales")
    names = [r["name"] for r in regs]
    assert "РегистрНакопления.ВитринаВыручка" in names
    assert "РегистрНакопления.ВитринаПерсонал" in names
    assert len(names) == 2


def test_get_all_registers(db_path):
    regs = get_all_registers()
    assert len(regs) == 3
