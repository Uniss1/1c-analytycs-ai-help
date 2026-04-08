"""Populate metadata.db with test dashboards, registers, and keywords."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "metadata.db"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
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
            id          INTEGER PRIMARY KEY,
            register_id INTEGER NOT NULL REFERENCES registers(id),
            name        TEXT NOT NULL,
            data_type   TEXT NOT NULL,
            description TEXT
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


def seed_data(cur: sqlite3.Cursor) -> None:
    # --- Dashboards ---
    cur.executemany(
        "INSERT OR IGNORE INTO dashboards (id, slug, title, url_pattern) VALUES (?, ?, ?, ?)",
        [
            (1, "sales", "Продажи", "/analytics/sales*"),
            (2, "costs", "Затраты", "/analytics/costs*"),
        ],
    )

    # --- Registers ---
    cur.executemany(
        "INSERT OR IGNORE INTO registers (id, name, description, register_type) VALUES (?, ?, ?, ?)",
        [
            (1, "РегистрНакопления.ВитринаВыручка",
             "Выручка по подразделениям и номенклатуре", "accumulation_turnover"),
            (2, "РегистрНакопления.ВитринаЗатрат",
             "Затраты по статьям и подразделениям", "accumulation_turnover"),
            (3, "РегистрНакопления.ВитринаПерсонал",
             "Численность и ФОТ по подразделениям", "accumulation_turnover"),
        ],
    )

    # --- Dashboard <-> Register links ---
    cur.executemany(
        "INSERT OR IGNORE INTO dashboard_registers (dashboard_id, register_id, widget_title) VALUES (?, ?, ?)",
        [
            (1, 1, "Выручка по месяцам"),
            (1, 3, "Численность"),
            (2, 2, "Затраты по статьям"),
            (2, 3, "ФОТ по подразделениям"),
        ],
    )

    # --- Dimensions ---
    cur.executemany(
        "INSERT OR IGNORE INTO dimensions (register_id, name, data_type, description) VALUES (?, ?, ?, ?)",
        [
            (1, "Период", "Дата", "Период оборотов"),
            (1, "Подразделение", "Справочник.Подразделения", "Подразделение организации"),
            (1, "Номенклатура", "Справочник.Номенклатура", "Товар или услуга"),
            (2, "Период", "Дата", "Период оборотов"),
            (2, "Подразделение", "Справочник.Подразделения", "Подразделение организации"),
            (2, "СтатьяЗатрат", "Справочник.СтатьиЗатрат", "Статья затрат"),
            (3, "Период", "Дата", "Период оборотов"),
            (3, "Подразделение", "Справочник.Подразделения", "Подразделение организации"),
        ],
    )

    # --- Resources ---
    cur.executemany(
        "INSERT OR IGNORE INTO resources (register_id, name, data_type, description) VALUES (?, ?, ?, ?)",
        [
            (1, "Сумма", "Число", "Сумма выручки в рублях"),
            (1, "Количество", "Число", "Количество единиц"),
            (2, "Сумма", "Число", "Сумма затрат в рублях"),
            (3, "Численность", "Число", "Количество сотрудников"),
            (3, "ФОТ", "Число", "Фонд оплаты труда"),
        ],
    )

    # --- Keywords ---
    cur.executemany(
        "INSERT OR IGNORE INTO keywords (register_id, keyword) VALUES (?, ?)",
        [
            (1, "выручка"), (1, "продажи"), (1, "доход"), (1, "оборот"), (1, "revenue"),
            (2, "затраты"), (2, "расходы"), (2, "себестоимость"), (2, "costs"),
            (3, "персонал"), (3, "сотрудники"), (3, "численность"), (3, "фот"), (3, "зарплата"),
        ],
    )


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    create_schema(cur)
    seed_data(cur)
    conn.commit()
    conn.close()
    print(f"metadata.db seeded at {DB_PATH}")


if __name__ == "__main__":
    main()
