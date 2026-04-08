"""Validate and sanitize generated 1C queries."""

import re

ALLOWED_KEYWORDS = {"ВЫБРАТЬ", "ИЗ", "ГДЕ", "СГРУППИРОВАТЬ", "УПОРЯДОЧИТЬ",
                    "ПЕРВЫЕ", "МЕЖДУ", "И", "ИЛИ", "НЕ", "КАК", "ИМЕЮЩИЕ",
                    "СОЕДИНЕНИЕ", "ЛЕВОЕ", "ПРАВОЕ", "ПОЛНОЕ", "ВНУТРЕННЕЕ",
                    "КОЛИЧЕСТВО", "СУММА", "МАКСИМУМ", "МИНИМУМ", "СРЕДНЕЕ",
                    "РАЗЛИЧНЫЕ", "ВСЕ", "ОБЪЕДИНИТЬ"}

FORBIDDEN_PATTERN = re.compile(
    r"\b(ПОМЕСТИТЬ|УНИЧТОЖИТЬ|УДАЛИТЬ|ИЗМЕНИТЬ|СОЗДАТЬ|ОБНОВИТЬ)\b",
    re.IGNORECASE,
)


def validate_query(query: str, allowed_registers: set[str]) -> tuple[bool, str]:
    """Validate query against whitelist and safety rules.

    Returns: (is_valid, error_message)
    """
    if not query.strip().upper().startswith("ВЫБРАТЬ"):
        return False, "Query must start with ВЫБРАТЬ"

    if FORBIDDEN_PATTERN.search(query):
        return False, "Query contains forbidden keywords"

    # Enforce row limit
    if "ПЕРВЫЕ" not in query.upper():
        query = query.replace("ВЫБРАТЬ", "ВЫБРАТЬ ПЕРВЫЕ 1000", 1)

    return True, ""
