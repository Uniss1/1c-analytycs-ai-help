"""Populate metadata.db from registers.yaml."""

import json
import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "metadata.db"
YAML_PATH = ROOT / "registers.yaml"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        DROP TABLE IF EXISTS keywords;
        DROP TABLE IF EXISTS resources;
        DROP TABLE IF EXISTS dimensions;
        DROP TABLE IF EXISTS dashboard_registers;
        DROP TABLE IF EXISTS registers;
        DROP TABLE IF EXISTS dashboards;

        CREATE TABLE IF NOT EXISTS dashboards (
            id          INTEGER PRIMARY KEY,
            slug        TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            url_pattern TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS registers (
            id            INTEGER PRIMARY KEY,
            name          TEXT NOT NULL UNIQUE,
            description   TEXT NOT NULL,
            register_type TEXT NOT NULL,
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS dashboard_registers (
            dashboard_id INTEGER NOT NULL REFERENCES dashboards(id),
            register_id  INTEGER NOT NULL REFERENCES registers(id),
            widget_title TEXT,
            PRIMARY KEY (dashboard_id, register_id)
        );

        CREATE TABLE IF NOT EXISTS dimensions (
            id             INTEGER PRIMARY KEY,
            register_id    INTEGER NOT NULL REFERENCES registers(id),
            name           TEXT NOT NULL,
            data_type      TEXT NOT NULL,
            description    TEXT,
            required       INTEGER NOT NULL DEFAULT 0,
            default_value  TEXT,
            filter_type    TEXT NOT NULL DEFAULT '=',
            allowed_values TEXT,
            technical      INTEGER NOT NULL DEFAULT 0,
            role           TEXT NOT NULL DEFAULT 'filter',
            description_en TEXT
        );

        CREATE TABLE IF NOT EXISTS resources (
            id          INTEGER PRIMARY KEY,
            register_id INTEGER NOT NULL REFERENCES registers(id),
            name        TEXT NOT NULL,
            data_type   TEXT NOT NULL DEFAULT 'Число',
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS keywords (
            id          INTEGER PRIMARY KEY,
            register_id INTEGER NOT NULL REFERENCES registers(id),
            keyword     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_keywords_word ON keywords(keyword);
        CREATE INDEX IF NOT EXISTS idx_dashboard_slug ON dashboards(slug);
    """)


def seed_from_yaml(cur: sqlite3.Cursor, data: dict) -> None:
    # --- Registers ---
    reg_id_map = {}
    for item in data.get("registers", []):
        # Support both simple string and full dict format
        if isinstance(item, str):
            reg = {"name": item, "description": item.split(".")[-1], "type": "accumulation_turnover"}
        else:
            reg = item
        # Try update first, insert if not exists
        row = cur.execute("SELECT id FROM registers WHERE name = ?", (reg["name"],)).fetchone()
        if row:
            reg_id = row[0]
            cur.execute(
                "UPDATE registers SET description = ?, register_type = ?, updated_at = datetime('now') WHERE id = ?",
                (reg["description"], reg.get("type", "accumulation_turnover"), reg_id),
            )
        else:
            cur.execute(
                "INSERT INTO registers (name, description, register_type) VALUES (?, ?, ?)",
                (reg["name"], reg["description"], reg.get("type", "accumulation_turnover")),
            )
            reg_id = cur.lastrowid
        reg_id_map[reg["name"]] = reg_id

        # Clear old dimensions/resources/keywords for this register
        cur.execute("DELETE FROM dimensions WHERE register_id = ?", (reg_id,))
        cur.execute("DELETE FROM resources WHERE register_id = ?", (reg_id,))
        cur.execute("DELETE FROM keywords WHERE register_id = ?", (reg_id,))

        for dim in reg.get("dimensions", []):
            values = dim.get("values")
            allowed_values = json.dumps(values, ensure_ascii=False) if values else None
            cur.execute(
                "INSERT INTO dimensions (register_id, name, data_type, description, required, default_value, filter_type, allowed_values, technical, role, description_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reg_id,
                    dim["name"],
                    dim["data_type"],
                    dim.get("description"),
                    1 if dim.get("required") else 0,
                    dim.get("default"),
                    dim.get("filter_type", "="),
                    allowed_values,
                    1 if dim.get("technical") else 0,
                    dim.get("role", "filter"),
                    dim.get("description_en"),
                ),
            )

        for res in reg.get("resources", []):
            cur.execute(
                "INSERT INTO resources (register_id, name, data_type, description) VALUES (?, ?, ?, ?)",
                (reg_id, res["name"], res.get("data_type", "Число"), res.get("description")),
            )

        keywords = reg.get("keywords", [])
        # Auto-generate keywords from register name if none provided
        if not keywords:
            import re
            short = reg["name"].split(".")[-1]
            parts = re.findall(r"[А-ЯЁ][а-яё]+", short)
            keywords = [p.lower() for p in parts if p.lower() not in ("витрина", "регистр", "накопления")]
        for kw in keywords:
            cur.execute(
                "INSERT INTO keywords (register_id, keyword) VALUES (?, ?)",
                (reg_id, kw),
            )

    # --- Dashboards ---
    for dash in data.get("dashboards", []):
        row = cur.execute("SELECT id FROM dashboards WHERE slug = ?", (dash["slug"],)).fetchone()
        if row:
            dash_id = row[0]
            cur.execute(
                "UPDATE dashboards SET title = ?, url_pattern = ?, updated_at = datetime('now') WHERE id = ?",
                (dash["title"], dash["url_pattern"], dash_id),
            )
        else:
            cur.execute(
                "INSERT INTO dashboards (slug, title, url_pattern) VALUES (?, ?, ?)",
                (dash["slug"], dash["title"], dash["url_pattern"]),
            )
            dash_id = cur.lastrowid

        cur.execute("DELETE FROM dashboard_registers WHERE dashboard_id = ?", (dash_id,))
        for link in dash.get("registers", []):
            reg_id = reg_id_map.get(link["name"])
            if reg_id:
                cur.execute(
                    "INSERT INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (?, ?, ?)",
                    (dash_id, reg_id, link.get("widget_title")),
                )
            else:
                print(f"  WARNING: register '{link['name']}' not found, skipping dashboard link")


def main() -> None:
    if not YAML_PATH.exists():
        example = YAML_PATH.parent / "registers.example.yaml"
        print(f"ERROR: {YAML_PATH} not found")
        if example.exists():
            print(f"  Скопируйте шаблон: cp {example} {YAML_PATH}")
        return

    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    create_schema(cur)
    seed_from_yaml(cur, data)
    conn.commit()
    conn.close()

    reg_count = len(data.get("registers", []))
    dash_count = len(data.get("dashboards", []))
    print(f"metadata.db seeded: {reg_count} registers, {dash_count} dashboards → {DB_PATH}")


if __name__ == "__main__":
    main()
