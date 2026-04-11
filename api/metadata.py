"""Metadata index for dashboard registers.

Stores mapping: dashboard -> registers -> dimensions/resources.
Populated by scripts/sync_metadata.py from 1C Analytics.
"""

import json
import logging
import sqlite3
import re

logger = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None

STOP_WORDS = {
    "какой", "какая", "какое", "какие", "сколько", "покажи", "выведи",
    "дай", "за", "по", "на", "из", "для", "что", "как", "где", "когда",
    "мне", "нам", "все", "всё", "это", "тот", "эта", "эти", "этот",
    "период", "месяц", "квартал", "год", "неделя", "день",
    "первый", "второй", "третий", "четвёртый", "последний",
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
    "а", "и", "в", "с", "к", "о", "у", "не",
}

_word_re = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


def init_metadata(db_path: str) -> None:
    """Connect to metadata.db."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(db_path)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")


def _get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Call init_metadata(db_path) first")
    return _conn


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a question."""
    words = _word_re.findall(text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def _enrich_register(row: sqlite3.Row) -> dict:
    """Add dimensions and resources to a register row."""
    conn = _get_conn()
    reg_id = row["id"]
    dims = conn.execute(
        "SELECT name, data_type, description, required, default_value, filter_type, allowed_values, technical, role, description_en "
        "FROM dimensions WHERE register_id = ?",
        (reg_id,),
    ).fetchall()
    ress = conn.execute(
        "SELECT name, data_type, description FROM resources WHERE register_id = ?",
        (reg_id,),
    ).fetchall()

    enriched_dims = []
    for d in dims:
        dim_dict = dict(d)
        # Convert required from int to bool
        dim_dict["required"] = bool(dim_dict.get("required"))
        dim_dict["technical"] = bool(dim_dict.get("technical"))
        # Parse allowed_values from JSON string to list
        av = dim_dict.get("allowed_values")
        if av:
            dim_dict["allowed_values"] = json.loads(av)
        else:
            dim_dict["allowed_values"] = []
        enriched_dims.append(dim_dict)

    return {
        "name": row["name"],
        "description": row["description"],
        "register_type": row["register_type"],
        "dimensions": enriched_dims,
        "resources": [dict(r) for r in ress],
    }


def find_register(question: str, dashboard_context: dict | None = None) -> tuple[dict | None, dict]:
    """Find relevant register by question keywords + dashboard context.

    Returns (register_metadata | None, debug_info).
    """
    conn = _get_conn()
    words = _extract_keywords(question)

    # Collect all available keywords in DB
    all_kw = conn.execute("SELECT k.keyword, r.name FROM keywords k JOIN registers r ON r.id=k.register_id").fetchall()
    kw_to_register = {}
    for row in all_kw:
        kw_to_register.setdefault(row[0], []).append(row[1])

    matching = {w: kw_to_register[w] for w in words if w in kw_to_register}

    debug_info = {
        "question": question,
        "extracted_words": words,
        "available_keywords": dict(kw_to_register),
        "matching_keywords": matching,
        "dashboard_slug": dashboard_context.get("slug") if dashboard_context else None,
    }

    logger.info("METADATA lookup: question=%r", question)
    logger.info("METADATA extracted words: %s", words)
    logger.info("METADATA matching: %s", matching)

    if not words:
        logger.warning("METADATA: no keywords extracted from question")
        debug_info["result"] = "no_keywords"
        return None, debug_info

    placeholders = ",".join("?" for _ in words)

    if dashboard_context and "slug" in dashboard_context:
        query = f"""
            SELECT r.*, COUNT(*) as hits
            FROM registers r
            JOIN keywords k ON k.register_id = r.id
            JOIN dashboard_registers dr ON dr.register_id = r.id
            JOIN dashboards d ON d.id = dr.dashboard_id
            WHERE k.keyword IN ({placeholders})
              AND d.slug = ?
            GROUP BY r.id
            ORDER BY hits DESC
            LIMIT 1
        """
        row = conn.execute(query, (*words, dashboard_context["slug"])).fetchone()
    else:
        query = f"""
            SELECT r.*, COUNT(*) as hits
            FROM registers r
            JOIN keywords k ON k.register_id = r.id
            WHERE k.keyword IN ({placeholders})
            GROUP BY r.id
            ORDER BY hits DESC
            LIMIT 1
        """
        row = conn.execute(query, words).fetchone()

    if row is None:
        # Fallback: если регистр в базе всего один — использовать его
        all_regs = conn.execute("SELECT * FROM registers").fetchall()
        if len(all_regs) == 1:
            logger.info("METADATA: no keyword match, but only 1 register — using it as fallback")
            result = _enrich_register(all_regs[0])
            debug_info["result"] = result["name"]
            debug_info["fallback"] = "single_register"
            return result, debug_info
        logger.warning("METADATA: no register found for words=%s", words)
        debug_info["result"] = "not_found"
        return None, debug_info
    result = _enrich_register(row)
    logger.info("METADATA found: %s (dims=%s, resources=%s)",
                result["name"],
                [d["name"] for d in result.get("dimensions", [])],
                [r["name"] for r in result.get("resources", [])])
    debug_info["result"] = result["name"]
    return result, debug_info


def get_all_registers() -> list[dict]:
    """Return all registers with their dimensions and resources."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM registers ORDER BY name").fetchall()
    return [_enrich_register(r) for r in rows]


def get_dashboard_registers(dashboard_slug: str) -> list[dict]:
    """Return registers linked to a specific dashboard."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT r.*
        FROM registers r
        JOIN dashboard_registers dr ON dr.register_id = r.id
        JOIN dashboards d ON d.id = dr.dashboard_id
        WHERE d.slug = ?
        ORDER BY r.name
        """,
        (dashboard_slug,),
    ).fetchall()
    return [_enrich_register(r) for r in rows]
