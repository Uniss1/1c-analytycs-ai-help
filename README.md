# 1C Analytics AI Help

**AI-помощник для 1С Аналитики — отвечает на вопросы по данным и методологии на естественном языке.**

Пользователь задаёт вопрос в чате (виджет внутри дашборда или standalone web-чат), система определяет тип вопроса, вызывает нужный инструмент через Gemma 4 E2B, отправляет JSON-параметры в 1С HTTP-сервис и возвращает отформатированный ответ.

## Возможности

- **7 инструментов запросов** — модель выбирает подходящий через tool calling:
  - `aggregate` — одно число за период ("Какая выручка за март?")
  - `group_by` — разбивка по измерению ("Выручка по ДЗО")
  - `top_n` — ранжирование ("Топ-5 ДЗО по выручке")
  - `time_series` — динамика по месяцам ("Тренд EBITDA помесячно")
  - `compare` — сравнение двух значений ("Факт vs план за март")
  - `ratio` — отношение показателей ("Рентабельность = маржа / выручка")
  - `filtered` — фильтрация по порогу ("ДЗО где выручка > 100 млн")
- **Вопросы по методологии** — поиск в базе знаний (Wiki.js + RAG)
- **Контекст дашборда** — виджет автоматически передаёт контекст текущего дашборда
- **Debug-панель** — в web UI видно какой инструмент вызвала модель, параметры, результат
- **Безопасность** — текст запроса 1С нигде не передаётся по сети, только JSON-параметры

## Быстрый старт

```bash
git clone https://github.com/Uniss1/1c-analytics-ai-help.git
cd 1c-analytics-ai-help
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Отредактировать .env — указать адреса Ollama, 1С, ai-chat

python3 scripts/seed_metadata.py
uvicorn api.main:app --reload --port 8000
curl http://localhost:8000/health
# → {"status": "ok"}
```

Web-чат: `http://localhost:8000/web/`

## Тесты

```bash
pytest tests/ -v
# 65 passed
```

Калибровка tool calling (требует Ollama с Gemma 4 E2B):

```bash
python3 scripts/calibrate_tools.py --model gemma4:e2b -v
# 18 тестовых кейсов: выбор инструмента + заполнение параметров
```

## Архитектура

```
Пользователь → виджет / web-чат
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
      tool_caller.py
      Gemma 4 E2B → tool calling
            ↓
      param_validator.py
      быстрая проверка JSON
            ↓
      onec_client.py → 1С HTTP-сервис
      POST /analytics/execute (JSON)
            ↓
      formatter.py (LLM) → ответ
```

1С HTTP-сервис принимает JSON с инструментом и параметрами, сам собирает и выполняет запрос на языке 1С. Спецификация эндпоинта: [`docs/1c-http-service-spec.md`](docs/1c-http-service-spec.md).

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.11+ |
| Ollama | 0.6+ |
| Gemma 4 E2B | 5.1B Q4_K_M (tool calling) |
| SQLite | 3.x (встроен в Python) |
| 1С Аналитика | с HTTP-сервисом `/analytics/execute` |
| ai-chat | Uniss1/ai-chat на порту 3001 |

## Стек

| Слой | Технологии |
|------|-----------|
| API | FastAPI, uvicorn, Pydantic Settings |
| Tool calling | Gemma 4 E2B через OpenAI-compatible API |
| Данные | SQLite (metadata + history), 1С HTTP-сервис |
| Знания | ai-chat (Wiki.js + pgvector + RAG) |
| Фронтенд | Vanilla JS виджет + standalone web-чат |
| Прокси | nginx (reverse proxy, script injection) |

## Структура проекта

```
api/
├── main.py             # FastAPI entrypoint, chat flow
├── config.py           # Pydantic Settings (.env)
├── tool_defs.py        # 7 инструментов (JSON Schema для Gemma)
├── tool_caller.py      # Вызов Gemma через OpenAI API + нормализация
├── param_validator.py  # Валидация JSON-параметров до отправки в 1С
├── onec_client.py      # HTTP-клиент 1С (execute_tool + execute_query)
├── metadata.py         # Поиск регистра по ключевым словам
├── router.py           # Классификация intent (data / knowledge)
├── formatter.py        # Форматирование ответа через LLM
├── llm_client.py       # Клиент Ollama (multi-GPU)
├── wiki_client.py      # Клиент ai-chat (база знаний)
├── history.py          # История чата SQLite
├── date_parser.py      # Парсинг периодов из русского текста
├── query_templates.py  # Шаблоны частых запросов (legacy)
└── query_generator.py  # Генерация запросов через LLM (legacy)
scripts/
├── calibrate_tools.py  # Калибровка tool calling (18 кейсов)
├── seed_metadata.py    # Заполнение metadata.db тестовыми данными
└── sync_metadata.py    # Синхронизация из 1С
tests/                  # 65 pytest тестов
web/                    # Standalone web-чат с debug-панелью
widget/                 # Виджет для встраивания в 1С Аналитику
docs/                   # Спецификации и планы
```
