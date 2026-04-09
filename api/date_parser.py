"""Extract date periods from Russian natural language text."""

import re
from datetime import date, timedelta
from calendar import monthrange

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "марта": 3,
    "апрел": 4, "ма": 5, "мая": 5, "июн": 6, "июля": 6,
    "июл": 7, "август": 8, "сентябр": 9,
    "октябр": 10, "ноябр": 11, "декабр": 12,
}

QUARTER_MAP = {"1": (1, 3), "2": (4, 6), "3": (7, 9), "4": (10, 12)}

# "за январь-март", "за февраль-июнь"
_MONTH_RANGE_RE = re.compile(
    r"за\s+(\w+)\s*[-–—]\s*(\w+)(?:\s+(\d{4}))?", re.IGNORECASE
)

# "за 1 квартал 2025", "за 2 кв 2025", "за Q1 2025", "за Q1"
_QUARTER_RE = re.compile(
    r"за\s+(?:(\d)\s*(?:квартал|кв\.?)|[qQкК](\d))(?:\s+(\d{4}))?",
    re.IGNORECASE,
)

# "за март 2025", "за март"
_MONTH_RE = re.compile(
    r"за\s+(\w+?)(?:\s+(\d{4}))?\b", re.IGNORECASE
)

# "за 2024 год", "за 2024"
_YEAR_RE = re.compile(
    r"за\s+(\d{4})(?:\s*г(?:од)?\.?)?\b", re.IGNORECASE
)

# "за последний месяц", "за прошлый месяц"
_LAST_MONTH_RE = re.compile(
    r"за\s+(?:последний|прошлый|предыдущий)\s+месяц", re.IGNORECASE
)


def _match_month(word: str) -> int | None:
    """Match a Russian month name (or prefix) to month number."""
    w = word.lower()
    for prefix, num in MONTHS.items():
        if w.startswith(prefix):
            return num
    return None


def _month_range(year: int, month: int) -> tuple[date, date]:
    _, last_day = monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def parse_period(text: str) -> dict | None:
    """Extract date period from Russian text.

    Returns: {"Начало": "YYYY-MM-DD", "Конец": "YYYY-MM-DD"} or None.
    """
    today = date.today()
    current_year = today.year

    # "за последний месяц"
    if _LAST_MONTH_RE.search(text):
        first_of_this = today.replace(day=1)
        last_month_end = first_of_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return _fmt(last_month_start, last_month_end)

    # "за январь-март 2025"
    m = _MONTH_RANGE_RE.search(text)
    if m:
        m1 = _match_month(m.group(1))
        m2 = _match_month(m.group(2))
        if m1 and m2:
            year = int(m.group(3)) if m.group(3) else current_year
            start = date(year, m1, 1)
            _, last_day = monthrange(year, m2)
            end = date(year, m2, last_day)
            return _fmt(start, end)

    # "за 1 квартал 2025", "за Q1"
    m = _QUARTER_RE.search(text)
    if m:
        q = m.group(1) or m.group(2)
        if q in QUARTER_MAP:
            year = int(m.group(3)) if m.group(3) else current_year
            start_month, end_month = QUARTER_MAP[q]
            start = date(year, start_month, 1)
            _, last_day = monthrange(year, end_month)
            end = date(year, end_month, last_day)
            return _fmt(start, end)

    # "за 2024 год"
    m = _YEAR_RE.search(text)
    if m:
        year = int(m.group(1))
        return _fmt(date(year, 1, 1), date(year, 12, 31))

    # "за март 2025", "за март"
    m = _MONTH_RE.search(text)
    if m:
        month = _match_month(m.group(1))
        if month:
            year = int(m.group(2)) if m.group(2) else current_year
            start, end = _month_range(year, month)
            return _fmt(start, end)

    return None


def _fmt(start: date, end: date) -> dict:
    return {"Начало": start.isoformat(), "Конец": end.isoformat()}
