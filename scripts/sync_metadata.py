#!/usr/bin/env python3
"""Discover 1C registers via HTTP service and populate metadata.db.

Connects to the 1C HTTP service, probes registers, extracts column names
and distinct dimension values, then writes everything to metadata.db.

Usage:
    python3 scripts/sync_metadata.py

Reads ONEC_BASE_URL, ONEC_USER, ONEC_PASSWORD from .env (or environment).
"""

import re
import sqlite3
import sys
from pathlib import Path

import httpx

# Add project root to path for config import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.config import settings

DB_PATH = Path(__file__).resolve().parent.parent / "metadata.db"

# --- Registers to probe ---
# Add your register names here. The script will skip those that don't exist.
REGISTERS_TO_PROBE = [
    "РегистрНакопления.ВитринаВыручка",
    "РегистрНакопления.ВитринаЗатрат",
    "РегистрНакопления.ВитринаПерсонал",
    "РегистрНакопления.ВитринаПродажи",
    "РегистрНакопления.ВитринаПоказатели",
    "РегистрНакопления.ВитринаСводная",
    "РегистрНакопления.ВитринаБюджет",
]

# Fields that are numeric aggregatable values (resources)
KNOWN_RESOURCE_NAMES = {
    "Сумма", "Выручка", "ОЗП", "Количество", "Численность", "ФОТ",
    "Себестоимость", "Маржа", "Прибыль", "Затраты", "Доход", "Расход",
    "Значение", "Итого", "Бюджет", "Факт", "План", "Отклонение",
}

# Fields to skip (system/internal)
SKIP_FIELDS = {"номер_строки", "НомерСтроки", "Регистратор", "Активность", "ВидДвижения"}

# Dimension fields worth extracting distinct values for keywords
DIMENSION_KEYWORDS_FIELDS = {"Показатель", "ДЗО", "Сценарий", "КонтурПоказателя", "ПризнакДоход"}


def query_1c(query_text: str, params: dict | None = None) -> dict:
    """Execute query via 1C HTTP service."""
    url = f"{settings.onec_base_url}/query"
    with httpx.Client(timeout=60, auth=(settings.onec_user, settings.onec_password)) as client:
        resp = client.post(url, json={"query": query_text, "params": params or {}})
        resp.raise_for_status()
        return resp.json()


def probe_register(register_name: str) -> dict | None:
    """Try to query a register, return first row or None if doesn't exist."""
    try:
        result = query_1c(f"ВЫБРАТЬ ПЕРВЫЕ 1 * ИЗ {register_name}")
        if result.get("success") and result.get("data"):
            return result["data"][0]
        if result.get("success"):
            print(f"  {register_name}: пустой (0 строк)")
            return {}
        print(f"  {register_name}: ошибка — {result.get('error', '?')}")
        return None
    except Exception as e:
        print(f"  {register_name}: не найден ({e})")
        return None


def get_distinct_values(register_name: str, field: str, limit: int = 200) -> list[str]:
    """Get distinct values of a dimension field."""
    try:
        result = query_1c(
            f"ВЫБРАТЬ РАЗЛИЧНЫЕ ПЕРВЫЕ {limit} {field} ИЗ {register_name}"
        )
        if result.get("success") and result.get("data"):
            return [str(row.get(field, "")) for row in result["data"] if row.get(field)]
        return []
    except Exception:
        return []


def classify_fields(sample_row: dict) -> tuple[list[dict], list[dict]]:
    """Classify fields into dimensions and resources based on sample data."""
    dimensions = []
    resources = []

    for field_name, value in sample_row.items():
        if field_name in SKIP_FIELDS:
            continue

        if isinstance(value, (int, float)) and field_name in KNOWN_RESOURCE_NAMES:
            resources.append({"name": field_name, "data_type": "Число", "description": ""})
        elif isinstance(value, str) and "T" in value and len(value) >= 19:
            dimensions.append({"name": field_name, "data_type": "Дата", "description": ""})
        elif isinstance(value, (int, float)) and field_name not in DIMENSION_KEYWORDS_FIELDS:
            if any(kw in field_name.lower() for kw in ("месяц", "номер", "код")):
                dimensions.append({"name": field_name, "data_type": "Число", "description": ""})
            else:
                resources.append({"name": field_name, "data_type": "Число", "description": ""})
        else:
            dimensions.append({"name": field_name, "data_type": "Строка", "description": ""})

    return dimensions, resources


def generate_keywords(register_name: str, distinct_values: dict) -> list[str]:
    """Generate search keywords from register name and dimension values."""
    keywords = set()

    # From register name: ВитринаВыручка → выручка
    short_name = register_name.split(".")[-1]
    parts = re.findall(r"[А-ЯЁ][а-яё]+", short_name)
    for part in parts:
        kw = part.lower()
        if kw not in ("витрина", "регистр", "накопления"):
            keywords.add(kw)

    # From distinct values of key fields (Показатель, ДЗО, etc.)
    for field, values in distinct_values.items():
        for val in values:
            kw = val.strip().lower()
            if len(kw) >= 2 and kw not in ("", "-", "0"):
                keywords.add(kw)

    return sorted(keywords)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dashboards (
            id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL, url_pattern TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS registers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL, register_type TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS dashboard_registers (
            dashboard_id INTEGER NOT NULL REFERENCES dashboards(id),
            register_id INTEGER NOT NULL REFERENCES registers(id),
            widget_title TEXT, PRIMARY KEY (dashboard_id, register_id)
        );
        CREATE TABLE IF NOT EXISTS dimensions (
            id INTEGER PRIMARY KEY, register_id INTEGER NOT NULL REFERENCES registers(id),
            name TEXT NOT NULL, data_type TEXT NOT NULL, description TEXT
        );
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY, register_id INTEGER NOT NULL REFERENCES registers(id),
            name TEXT NOT NULL, data_type TEXT NOT NULL DEFAULT 'Число', description TEXT
        );
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY, register_id INTEGER NOT NULL REFERENCES registers(id),
            keyword TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_keywords_word ON keywords(keyword);
        CREATE INDEX IF NOT EXISTS idx_dashboard_slug ON dashboards(slug);
    """)


def save_register(conn: sqlite3.Connection, register_name: str,
                  dimensions: list[dict], resources: list[dict],
                  keywords: list[str]) -> int:
    """Save register and its metadata, return register_id."""
    cur = conn.cursor()
    short_name = register_name.split(".")[-1]

    cur.execute(
        """INSERT OR REPLACE INTO registers (name, description, register_type, updated_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (register_name, short_name, "accumulation_turnover"),
    )
    reg_id = cur.lastrowid

    # Clear old data for this register
    for table in ("dimensions", "resources", "keywords"):
        cur.execute(f"DELETE FROM {table} WHERE register_id = ?", (reg_id,))

    for dim in dimensions:
        cur.execute(
            "INSERT INTO dimensions (register_id, name, data_type, description) VALUES (?, ?, ?, ?)",
            (reg_id, dim["name"], dim["data_type"], dim.get("description", "")),
        )
    for res in resources:
        cur.execute(
            "INSERT INTO resources (register_id, name, data_type, description) VALUES (?, ?, ?, ?)",
            (reg_id, res["name"], res["data_type"], res.get("description", "")),
        )
    for kw in keywords:
        cur.execute(
            "INSERT INTO keywords (register_id, keyword) VALUES (?, ?)",
            (reg_id, kw),
        )

    conn.commit()
    return reg_id


def main():
    print(f"1C: {settings.onec_base_url}")
    print(f"User: {settings.onec_user}")
    print(f"DB: {DB_PATH}\n")

    # Test connection
    print("Подключение к 1С...")
    try:
        result = query_1c("ВЫБРАТЬ ПЕРВЫЕ 1 1 КАК Тест")
        if not result.get("success"):
            print(f"ОШИБКА: {result.get('error')}")
            sys.exit(1)
        print("OK\n")
    except Exception as e:
        print(f"ОШИБКА: {e}")
        sys.exit(1)

    # Probe registers
    print("Поиск регистров...")
    found = {}
    for name in REGISTERS_TO_PROBE:
        sample = probe_register(name)
        if sample is not None:
            found[name] = sample
            if sample:
                print(f"  ✓ {name} — {len(sample)} полей")

    if not found:
        print("\nНе найдено ни одного регистра.")
        print("Добавьте имена в REGISTERS_TO_PROBE в scripts/sync_metadata.py")
        sys.exit(1)

    print(f"\nНайдено: {len(found)}\n")

    # Init DB
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)

    # Process each register
    for reg_name, sample_row in found.items():
        print(f"--- {reg_name} ---")
        if not sample_row:
            print("  Пустой, пропускаю\n")
            continue

        dimensions, resources = classify_fields(sample_row)
        print(f"  Измерения: {[d['name'] for d in dimensions]}")
        print(f"  Ресурсы:   {[r['name'] for r in resources]}")

        # Distinct values for keyword-worthy dimensions
        distinct = {}
        for dim in dimensions:
            if dim["name"] in DIMENSION_KEYWORDS_FIELDS:
                values = get_distinct_values(reg_name, dim["name"])
                if values:
                    distinct[dim["name"]] = values
                    preview = values[:5]
                    print(f"  {dim['name']}: {len(values)} шт — {preview}{'...' if len(values) > 5 else ''}")

        keywords = generate_keywords(reg_name, distinct)
        print(f"  Keywords ({len(keywords)}): {keywords[:10]}{'...' if len(keywords) > 10 else ''}")

        reg_id = save_register(conn, reg_name, dimensions, resources, keywords)
        print(f"  Сохранено (id={reg_id})\n")

    conn.close()

    print("=" * 50)
    print(f"metadata.db готов: {DB_PATH}")
    print()
    print("Проверка:")
    print(f"  sqlite3 {DB_PATH} 'SELECT name FROM registers'")
    print(f"  sqlite3 {DB_PATH} 'SELECT r.name, k.keyword FROM keywords k JOIN registers r ON r.id=k.register_id'")
    print()
    print("Дашборды нужно добавить вручную (если есть):")
    print(f"  sqlite3 {DB_PATH} \"INSERT INTO dashboards (slug, title, url_pattern) VALUES ('main', 'Главный', '/analytics/main*')\"")


if __name__ == "__main__":
    main()
