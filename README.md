# 1C Analytics AI Help

**AI-помощник для 1С Аналитики — отвечает на вопросы по данным дашбордов на естественном языке.**

Пользователь задаёт вопрос в чате, SLM (Gemma 4 E2B / Qwen 3.5 4B через Ollama `/api/chat`) выбирает режим единого `query`-инструмента и заполняет JSON-параметры. Python нормализует и валидирует параметры, отправляет их в 1С HTTP-сервис, который сам собирает и выполняет запрос. Ответ форматируется по шаблону без LLM.

## Возможности

- **Один `query`-tool с `mode`-enum** — модель выбирает режим:
  - `aggregate` — одно число за период («Какая выручка за март 2025?»)
  - `group_by` — разбивка по измерению, в т.ч. top-N («Выручка по ДЗО»)
  - `compare` — два значения одного измерения side-by-side («Факт vs план»)

  На стороне 1С три режима маппятся в 7 типов запросов (`aggregate`, `group_by`,
  `top_n`, `time_series`, `compare`, `ratio`, `filtered`). Текст 1С-запроса
  никогда не пересекает сеть — только JSON-параметры.

- **Массивы значений в фильтрах** — «выручка у ДЗО-1 и ДЗО-2» → `company: ["ДЗО-1","ДЗО-2"]`. Скаляр модели тоже принимается и оборачивается в одноэлементный массив.

- **Опциональный `month`** — «выручка за 2024 год» (без указания месяца) даёт сумму за весь год.

- **Self-healing loop** — при провале валидации параметров текст ошибки
  возвращается модели, она перевызывает tool до 3 раз перед тем как
  переспросить пользователя. ADR: [`docs/decisions/2026-04-12-self-healing.md`](docs/decisions/2026-04-12-self-healing.md).

- **Debug-панель** — в web UI видно какой режим выбрала модель, параметры, результат, retry-цикл.

- **Безопасность** — текст 1С-запроса нигде не передаётся; параметры — single source of truth.

## Быстрый старт

```bash
git clone https://github.com/Uniss1/1c-analytics-ai-help.git
cd 1c-analytics-ai-help
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Отредактировать .env — указать OLLAMA_BASE_URL, MODEL_NAME, ONEC_*

cp registers.example.yaml registers.yaml
# Отредактировать registers.yaml под свой 1С-регистр

python3 scripts/seed_metadata.py
uvicorn api.main:app --reload --port 8000
curl http://localhost:8000/health
# → {"status": "ok"}
```

Web-чат: `http://localhost:8000/web/`

### Подключение нового регистра

Чек-лист в [`CLAUDE.md`](CLAUDE.md#adding-a-new-register). Кратко:

1. Имя регистра в `registers.yaml` — только идентификатор (`Витрина_Дашборда`),
   **без** префикса `РегистрСведений.` / `РегистрНакопления.`.
2. Для каждого нового измерения добавить Latin-маппинг в `_KEY_TO_DIM`
   (`api/tool_defs.py`).
3. Вручную проверить `technical: true/false` и `default:` для измерений (или
   запустить интерактивный `python3 scripts/sync_metadata.py`).
4. `python3 scripts/seed_metadata.py` → рестарт `uvicorn` → smoke в браузере.

## Тесты

```bash
pytest tests/ -v
```

Калибровка tool calling против реальной модели (требует Ollama):

```bash
python3 scripts/calibrate_tools.py -v
# Кейсы генерируются из метаданных регистра: aggregate/group_by/compare base +
# year-only + multi-value + declensions + typos + degraded.
```

## Архитектура

```
Пользователь → виджет / web-чат
                    ↓
              FastAPI (:8000)
                    ↓
      metadata.py
      find_register()
                    ↓
      tool_caller.py
      Ollama /api/chat + single query tool
                    ↓
      param_validator.py ←─┐
      проверка JSON         │ self-healing loop
                    ↓       │ (до 3 ретраев с feedback)
      (ok) ─────────────────┘
                    ↓
      onec_client.py → 1С HTTP-сервис
      POST /analytics_execute (JSON)
                    ↓
      answer_formatter.py (шаблон, без LLM) → ответ
```

LLM-роутер `data | knowledge` и интеграция с Wiki.js / `ai-chat` отключены —
`/chat` идёт напрямую через tool calling. `POST /knowledge` возвращает 503-заглушку.
План возврата (parked): [`docs/superpowers/plans/2026-04-13-restore-knowledge-endpoint.md`](docs/superpowers/plans/2026-04-13-restore-knowledge-endpoint.md).

Контракт 1С HTTP-сервиса: [`docs/1c-http-service-spec.md`](docs/1c-http-service-spec.md).
Эталонный BSL-код модуля: [`docs/1c-http-service-module.md`](docs/1c-http-service-module.md).

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.11+ |
| Ollama | 0.6+ |
| Модель | Gemma 4 E2B (5.1B) или Qwen 3.5 4B — обе с tool calling |
| SQLite | 3.x (встроен в Python) |
| 1С Аналитика | с HTTP-сервисом `/analytics_execute` |

## Стек

| Слой | Технологии |
|------|-----------|
| API | FastAPI, uvicorn, Pydantic Settings |
| Tool calling | SLM через Ollama native `/api/chat` (НЕ `/v1/chat/completions`) |
| Данные | SQLite (`metadata.db`, `history.db`), 1С HTTP-сервис |
| Фронтенд | Vanilla JS виджет + standalone web-чат |

## Структура проекта

```
api/
├── main.py             # FastAPI entrypoint, chat flow + self-healing loop
├── config.py           # Pydantic Settings (.env)
├── tool_defs.py        # Single query tool — JSON Schema, Latin keys, few-shot
├── tool_caller.py      # Ollama /api/chat + retry с validation feedback
├── param_validator.py  # Валидация JSON-параметров перед отправкой в 1С
├── filter_utils.py     # as_string_list — нормализация значений фильтров
├── onec_client.py      # HTTP-клиент 1С (execute_tool + execute_query)
├── metadata.py         # Поиск регистра по ключевым словам
├── answer_formatter.py # Шаблонное форматирование ответа (без LLM)
└── history.py          # История чата SQLite

scripts/
├── seed_metadata.py    # Заполнение metadata.db из registers.yaml
├── sync_metadata.py    # Discovery измерений/значений из 1С + интервью
├── calibrate_tools.py  # Калибровка tool calling против Ollama
├── calibration_cases.py # Data-driven генератор кейсов из метаданных
└── clear_history.py

tests/                  # pytest (unit + e2e, respx-мокинг Ollama и 1С)
web/                    # Standalone web-чат с debug-панелью
widget/                 # JS-виджет для встраивания в 1С Аналитику

docs/
├── 1c-http-service-spec.md   # Контракт /analytics_execute и /query
├── 1c-http-service-module.md # Эталонный BSL-код HTTP-сервиса
├── decisions/                # ADR (self-healing loop)
├── specs/                    # Top-level дизайн-доки
└── superpowers/              # Specs и planы для текущих/parked задач
```
