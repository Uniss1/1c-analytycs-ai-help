"""Pre-built query templates for common dashboard questions.

When a question matches a template, LLM is skipped entirely —
only parameter extraction is needed.
"""

TEMPLATES = [
    {
        "name": "sum_for_period",
        "patterns": ["выручка за", "сумма за", "оборот за", "итого за"],
        "template": """ВЫБРАТЬ ПЕРВЫЕ {limit}
    СУММА({resource}) КАК Значение
ИЗ
    РегистрНакопления.{register}.Обороты(&Начало, &Конец,,,)""",
        "params": ["Начало", "Конец"],
    },
    {
        "name": "sum_by_dimension",
        "patterns": ["по подразделениям", "по номенклатуре", "в разрезе"],
        "template": """ВЫБРАТЬ ПЕРВЫЕ {limit}
    {dimension} КАК Группировка,
    СУММА({resource}) КАК Значение
ИЗ
    РегистрНакопления.{register}.Обороты(&Начало, &Конец, ,,,)
СГРУППИРОВАТЬ ПО {dimension}
УПОРЯДОЧИТЬ ПО Значение УБЫВ""",
        "params": ["Начало", "Конец"],
    },
    {
        "name": "top_n",
        "patterns": ["топ", "лучших", "худших", "максимальн", "минимальн"],
        "template": """ВЫБРАТЬ ПЕРВЫЕ {n}
    {dimension} КАК Группировка,
    СУММА({resource}) КАК Значение
ИЗ
    РегистрНакопления.{register}.Обороты(&Начало, &Конец, ,,,)
СГРУППИРОВАТЬ ПО {dimension}
УПОРЯДОЧИТЬ ПО Значение {order}""",
        "params": ["Начало", "Конец"],
    },
]
