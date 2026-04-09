# 1C Analytics AI Help

**AI-помощник для пользователей 1С Аналитики — отвечает на вопросы по данным и методологии на естественном языке.**

Пользователь задаёт вопрос в чате (виджет внутри дашборда или standalone), система определяет тип вопроса (данные / методология), формирует запрос к 1С или базе знаний и возвращает ответ.

## Возможности

- Вопросы по данным: "Какая выручка за март?" — генерация запроса 1С, выполнение, форматирование ответа
- Вопросы по методологии: "Как считается маржинальность?" — поиск в базе знаний (Wiki.js + RAG)
- Контекст дашборда: виджет автоматически передаёт, на каком дашборде находится пользователь
- Шаблоны запросов: частые вопросы (сумма за период, по измерениям, топ-N) — без LLM
- Валидация: безопасность запросов (whitelist регистров, запрет модификации, лимит строк)

## Быстрый старт

```bash
# 1. Клонировать и установить зависимости
git clone https://github.com/Uniss1/1c-analytics-ai-help.git
cd 1c-analytics-ai-help
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest  # для тестов

# 2. Настроить окружение
cp .env.example .env
# Отредактировать .env — указать адреса Ollama, 1С, ai-chat

# 3. Заполнить metadata.db тестовыми данными
python3 scripts/seed_metadata.py

# 4. Запустить сервер
uvicorn api.main:app --reload --port 8000

# 5. Проверить
curl http://localhost:8000/health
# → {"status": "ok"}
```

## Как проверить (тесты)

```bash
# Все тесты
python3 -m pytest tests/ -v

# Ожидаемый результат (20 тестов):
# tests/test_metadata.py::test_find_register_by_keyword              PASSED
# tests/test_metadata.py::test_find_register_with_dashboard_context  PASSED
# tests/test_metadata.py::test_find_register_not_found               PASSED
# tests/test_metadata.py::test_get_dashboard_registers               PASSED
# tests/test_metadata.py::test_get_all_registers                     PASSED
# tests/test_query_generator_integration.py::test_template_path      PASSED
# tests/test_query_generator_integration.py::test_llm_path           PASSED
# tests/test_query_generator_integration.py::test_llm_invalid_query_raises PASSED
# tests/test_templates.py::test_sum_for_period                       PASSED
# tests/test_templates.py::test_sum_by_dimension                     PASSED
# tests/test_templates.py::test_top_n                                PASSED
# tests/test_templates.py::test_no_match                             PASSED
# tests/test_templates.py::test_date_parser_month                    PASSED
# tests/test_templates.py::test_date_parser_quarter                  PASSED
# tests/test_templates.py::test_date_parser_year                     PASSED
# tests/test_validator.py::test_valid_select                         PASSED
# tests/test_validator.py::test_reject_delete                        PASSED
# tests/test_validator.py::test_reject_unknown_register              PASSED
# tests/test_validator.py::test_add_limit                            PASSED
# tests/test_validator.py::test_keep_existing_limit                  PASSED
```

### Ручная проверка metadata

```bash
# Заполнить БД (если ещё не сделано)
python3 scripts/seed_metadata.py

# Проверить содержимое
sqlite3 metadata.db "SELECT r.name, k.keyword FROM registers r JOIN keywords k ON k.register_id = r.id"
# → 14 строк: ключевые слова → регистры

sqlite3 metadata.db "SELECT d.title, r.name FROM dashboards d JOIN dashboard_registers dr ON dr.dashboard_id = d.id JOIN registers r ON r.id = dr.register_id"
# → 4 строки: связи дашбордов с регистрами
```

### Ручная проверка в Python

```python
from api.metadata import init_metadata, find_register
init_metadata("metadata.db")

# Поиск регистра по ключевому слову
result = find_register("какая выручка за март?")
print(result["name"])        # → РегистрНакопления.ВитринаВыручка
print(result["dimensions"])  # → [{name: Период, ...}, {name: Подразделение, ...}, ...]

# С контекстом дашборда
result = find_register("численность", dashboard_context={"slug": "costs"})
print(result["name"])        # → РегистрНакопления.ВитринаПерсонал

# Несуществующий запрос
result = find_register("погода завтра")
print(result)                # → None
```

```python
from api.date_parser import parse_period

# Парсинг периодов из текста
parse_period("за март")              # → {"Начало": "2025-03-01", "Конец": "2025-03-31"}
parse_period("за 1 квартал 2025")    # → {"Начало": "2025-01-01", "Конец": "2025-03-31"}
parse_period("за 2024 год")          # → {"Начало": "2024-01-01", "Конец": "2024-12-31"}
parse_period("за последний месяц")   # → предыдущий календарный месяц
parse_period("за январь-март 2025")  # → {"Начало": "2025-01-01", "Конец": "2025-03-31"}
```

```python
from api.query_templates import try_match

meta = find_register("выручка за март")
result = try_match("выручка за март", meta)
print(result["query"])   # → ВЫБРАТЬ ПЕРВЫЕ 1000 СУММА(Сумма) КАК Значение ИЗ ...
print(result["params"])  # → {"Начало": "2025-03-01", "Конец": "2025-03-31"}

# Нет подходящего шаблона — вернёт None, уйдёт в LLM
try_match("сравни Q1 и Q2", meta)  # → None
```

```python
from api.query_validator import validate_query

whitelist = {"РегистрНакопления.ВитринаВыручка"}

# Валидный запрос
ok, err, query = validate_query(
    "ВЫБРАТЬ Сумма ИЗ РегистрНакопления.ВитринаВыручка.Обороты(,,,)",
    whitelist
)
print(ok, query)  # → True  ВЫБРАТЬ ПЕРВЫЕ 1000 Сумма ИЗ ...

# Запрещённая операция
ok, err, query = validate_query("УДАЛИТЬ ИЗ Таблица", whitelist)
print(ok, err)    # → False  Запрещено: УДАЛИТЬ

# Регистр не в whitelist
ok, err, query = validate_query(
    "ВЫБРАТЬ * ИЗ РегистрНакопления.СекретныйРегистр.Обороты(,,,)",
    whitelist
)
print(ok, err)    # → False  Регистр не из разрешенного списка: ...
```

## Архитектура

```
Пользователь → виджет/web-чат
                    ↓
              nginx (rate limit)
                    ↓
              FastAPI (:8000)
                    ↓
            ┌── Router (LLM) ──┐
            ↓                  ↓
         "data"           "knowledge"
            ↓                  ↓
      metadata.py         wiki_client.py
      find_register()     → ai-chat сервис
            ↓
   query_templates / query_generator (LLM)
            ↓
    query_validator.py
            ↓
    onec_client.py → 1С HTTP-сервис
            ↓
    formatter.py (LLM) → ответ
```

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.12+ |
| Ollama | 0.6+ (4 инстанса, по одному на GPU) |
| SQLite | 3.x (встроен в Python) |
| 1С Аналитика | с HTTP-сервисом для запросов |
| ai-chat | Uniss1/ai-chat на порту 3001 |

## Стек

| Слой | Технологии |
|------|-----------|
| API | FastAPI, uvicorn, Pydantic Settings |
| LLM | Ollama, Qwen 3.5 4B (4 GPU) |
| Данные | SQLite (metadata + history), 1С HTTP-сервис |
| Знания | ai-chat (Wiki.js + pgvector + RAG) |
| Фронтенд | Vanilla JS виджет, HTML чат |
| Прокси | nginx (reverse proxy, script injection) |

## Структура проекта

```
api/                  # FastAPI backend
├── main.py           # Entrypoint, CORS, static mounts
├── config.py         # Pydantic Settings (.env)
├── metadata.py       # Поиск регистра по ключевым словам ✅
├── query_validator.py # Валидация запросов 1С ✅
├── query_templates.py # Шаблоны частых запросов + try_match() ✅
├── query_generator.py # Генерация запросов: шаблоны → LLM fallback ✅
├── date_parser.py    # Парсинг периодов из русского текста ✅
├── router.py         # Классификация intent (stub)
├── formatter.py      # Форматирование ответа через LLM (stub)
├── llm_client.py     # Клиент Ollama (multi-GPU)
├── onec_client.py    # Клиент HTTP-сервиса 1С
├── wiki_client.py    # Клиент ai-chat
└── history.py        # История чата SQLite (stub)
scripts/
├── seed_metadata.py  # Заполнение metadata.db тестовыми данными ✅
└── sync_metadata.py  # Синхронизация из 1С (stub)
tests/                # pytest тесты ✅
prompts/              # Системные промпты для LLM
web/                  # Standalone web-чат
widget/               # Виджет для встраивания в 1С Аналитику
nginx/                # Конфигурация reverse proxy
docs/                 # Спецификации и планы
```

## Статус реализации

| Фаза | Описание | Статус |
|------|----------|--------|
| 0 | Инфраструктура (Ollama, FastAPI, SQLite) | Done |
| 1 | Data flow: metadata, validation | Done |
| 1.5 | Data flow: query generation (templates + LLM) | Done |
| 2 | Knowledge flow (ai-chat интеграция) | Pending |
| 3 | Router + Chat API | Pending |
| 4 | Расширение 1С + подключение | Pending |
| 5 | Виджет + nginx + web UI | Pending |
