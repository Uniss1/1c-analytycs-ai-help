# 1C Analytics AI Help

AI-assistant for 1C Analytics dashboards. Users ask questions in Russian, system selects a tool via Gemma 4 E2B, sends JSON params to 1C HTTP service, returns formatted answer.

## Critical Rules

- **All LLM prompts in English.** `answer_formatter.py` uses Russian templates (no LLM).
- **No hardcoded register metadata.** Always load from `metadata.db` via `get_all_registers()`.
- **registers.yaml is gitignored.** Template: `registers.example.yaml`. Never commit real registers.
- **Tool caller uses Ollama native API** (`/api/chat`), NOT `/v1/chat/completions`. The OpenAI-compatible endpoint breaks tool calling for Gemma.
- **Dimension keys must be Latin** in tool schemas. Cyrillic in JSON Schema confuses small models. See `_dim_key()` mapping in `tool_defs.py`.
- **Exclude technical dimensions** from tool schemas. Mark them with `technical: true` in `registers.yaml` via `sync_metadata.py` interview. Fallback hardcoded list: `Показатель_номер`, `Ед_изм`, `Масштаб`, `Месяц`, `ПризнакДоход`.
- **1C query text never crosses network.** Only JSON params. 1C builds queries internally.

## Commands

```bash
# Dev
uvicorn api.main:app --reload --port 8000

# Tests
pytest tests/ -v

# Metadata: seed from YAML
python3 scripts/seed_metadata.py

# Metadata: sync from 1C (discovers dimensions/resources)
python3 scripts/sync_metadata.py

# Calibrate tool calling (needs Ollama + gemma4:e2b)
python3 scripts/calibrate_tools.py -v
```

## Architecture

```
User → nginx → FastAPI(:8000)
                  ↓
           metadata.py
           find_register()
                  ↓
           tool_caller.py
           Ollama /api/chat + tools (with self-healing retries)
                  ↓
           param_validator.py
                  ↓
           onec_client.py → POST /analytics_execute (JSON)
                  ↓
           answer_formatter.py (template) → answer in Russian
```

The LLM router (`data`/`knowledge`) and the wiki/`ai-chat` integration were
removed to keep `/chat` latency tight and focus on tool-calling stability.
`POST /knowledge` is a 503 stub. Plan to restore:
`docs/superpowers/plans/2026-04-13-restore-knowledge-endpoint.md`.

## Key Files

| Path | Purpose |
|------|---------|
| `api/tool_defs.py` | Single `query` tool schema with `mode` enum (Latin keys, enum values in Russian) |
| `api/tool_caller.py` | Ollama `/api/chat` with tools, retry logic, normalization to 1C params |
| `api/param_validator.py` | Validate JSON params before sending to 1C |
| `api/answer_formatter.py` | Template-based formatting of 1C results into Russian text (no LLM) |
| `api/metadata.py` | Register lookup by keywords. **Single-register fallback**: if only 1 register in DB, uses it without keyword match |
| `api/onec_client.py` | HTTP client to 1C `/analytics_execute` |
| `api/config.py` | Pydantic Settings from `.env`. Default model: `gemma4:e2b` |
| `registers.example.yaml` | Template for register metadata (gitignored `registers.yaml` is the real config) |
| `scripts/seed_metadata.py` | Drops and recreates all tables from `registers.yaml` |
| `docs/1c-http-service-spec.md` | Contract for 1C HTTP service endpoint |
| `docs/1c-http-service-module.md` | Full BSL code for 1C HTTP service module |

## Single Query Tool (tool_defs.py)

One `query` tool with a `mode` enum replaces the previous 7 separate tools. Model picks the mode and fills parameters in one call. This reduces confusion for the 5B model — one tool to call, always.

| Mode | Use case | Extra params |
|------|----------|--------------|
| `aggregate` | Single sum for period | — |
| `group_by` | Breakdown by dimension (also top-N) | group_by |
| `compare` | Two values side by side | compare_by, compare_values (2) |

**Required params:** `mode`, `resource`, `year` (`month` опционален — отсутствие = запрос за весь год)
**Filter dimensions** (auto-generated from metadata): `scenario`, `contour`, `metric`, `company`, etc. Каждый фильтр — **массив строк**: `["Факт"]` для одного значения, `["ДЗО-1","ДЗО-2"]` для нескольких.

The 1C HTTP service still handles all 7 tool types (aggregate, group_by, top_n, time_series, compare, ratio, filtered). `tool_caller.py` normalizes mode→tool mapping before sending to 1C.

## Dimension Key Mapping (tool_defs.py)

Model sees Latin keys, 1C gets Cyrillic. Both directions via `_dim_key()` / `key_to_dim()`:

```
Сценарий ↔ scenario    КонтурПоказателя ↔ contour
Показатель ↔ metric    ДЗО ↔ company
Масштаб ↔ scale        Подразделение ↔ department
ПризнакДоход ↔ income_flag    Ед_изм ↔ unit
Месяц ↔ period_month   Показатель_номер ↔ metric_number
```

**When adding new registers:** add Latin mappings for ALL dimensions. Missing mappings = Cyrillic in schema = broken tool calling.

## Adding a new register

Чек-лист при подключении нового регистра из 1С:

1. **Имя регистра в `registers.yaml`** — только идентификатор (`Витрина_Выручка`),
   **без** префикса `РегистрСведений.` / `РегистрНакопления.`. Префикс 1С
   собирает из поля `type`. Префикс в `name` ломает маршрутизацию в BSL-модуле.

2. **Latin-ключи для каждого нового измерения.** Добавить маппинг
   `<Русское имя> ↔ <latin_key>` в `_KEY_TO_DIM` и `_dim_key()` (`api/tool_defs.py`).
   Пропущенный маппинг = кириллица в JSON Schema = сломанный tool calling у SLM.

3. **Вручную проверить `technical` и `default`** в `registers.yaml`:
   - `technical: true` — поле скрывается от модели (вспомогательные измерения
     вроде `Масштаб`, `Ед_изм`, `Показатель_номер`). Без этого модель пытается
     их заполнять — лишние уточнения у пользователя.
   - `default: <значение>` — подставляется автоматически в `filters`, если
     пользователь не указал. Required-измерение без `default` заставляет
     бэкенд переспрашивать.
   Альтернатива ручной правке — интервью `python3 scripts/sync_metadata.py`.

4. **Проверить `.env`** перед калибровкой: `cat .env | grep MODEL_NAME` —
   модель в `.env` может расходиться с докой.

5. **Запустить** `python3 scripts/seed_metadata.py` → рестарт `uvicorn` →
   реальный вопрос в браузере (правило «CI-зелёный ≠ работает в браузере»).

## Config (.env)

```bash
OLLAMA_BASE_URL=http://localhost:11434  # Ollama address
MODEL_NAME=gemma4:e2b                   # Tool calling model
ONEC_BASE_URL=http://1c-server/base/hs/ai
ONEC_USER=ai_assistant
ONEC_PASSWORD=
```

## 1C HTTP Service

Full BSL module code: `docs/1c-http-service-module.md`
Spec: `docs/1c-http-service-spec.md`
Platform XML specs: `docs/1c-platform-specs/` (from cc-1c-skills)

One module in Конфигуратор → HTTP-сервисы → `АИАналитика` → Module.bsl

## Known Issues / TODO

- **Metadata enrichment**: `sync_metadata.py` has interactive interview mode. Run it to annotate new fields with `technical`/`role`/`description_en`. `tool_defs.py` reads annotations from metadata (falls back to hardcoded list for unannotated registers).
- **Ollama port**: production server uses `10.10.90.188:11443`, not default 11434.

## Lessons learned

Уроки 4 дней постройки. Каждое правило — императив + 1 строка «почему».

**SLM tool calling (5B-класс моделей):**
- Один tool с `mode` enum > 7 разных tools — модель путается в выборе имени функции
- Латинские ключи в JSON Schema > кириллические — кириллица в schema ломает small models
- Enum-значения и дефолты дублируем и в schema, и в system message — двойное подкрепление
- Перед бенчмарком: `cat .env | grep MODEL_NAME` — не доверять докам, .env может расходиться
- Ollama native `/api/chat`, не OpenAI-compat `/v1/chat/completions` — для Gemma тулы ломаются
- Один tool (`query` с `mode` enum) маскирует ошибки выбора функции — даже Qwen3.5:2b не промахивается, потому что промахнуться некуда. При добавлении 2+ tools обязательна повторная калибровка: без неё проблема всплывёт на проде
- Фильтры передаём массивом (`["ДЗО-1","ДЗО-2"]`), даже для одного значения. Скаляр 1С принимает для совместимости, но единая форма упрощает промпт и BSL-сборку `В (&Знач)`
- `month` опционален: «выручка за 2024 год» = `year=2024` без `month`. Few-shot пример учит модель опускать поле, не ставить `12`

**Архитектурные:**
- Текст 1С-запроса не пересекает сеть. Только JSON params (безопасность + корректность синтаксиса бесплатно)
- Правило, живущее в двух местах → выноси helper. Technical-dim фикс ловили дважды
- Не ставить LLM туда, где работает шаблон. Каждый LLM-вызов = +1–3 сек латентности
- Чинить root cause, не симптом: `invalid_params` на корректный resource = баг валидатора, не клиента

**1С платформа (часто граблями):**
- URL-шаблоны HTTP-сервиса односегментные: `/analytics_execute`, не `/analytics/execute`
- РегистрСведений имеет три коллекции: `Измерения`, `Ресурсы`, `Реквизиты` — проверять обе при поиске поля
- Оператор сравнения подставлять через switch/case, не конкатенацией строки (защита от инъекций)
- `ЗаписатьJSON` не умеет ссылочные типы (`СправочникСсылка.*`, `ПеречислениеСсылка.*`). Если измерение регистра типизировано ссылкой, ячейка `ТаблицыЗначений` из `Выгрузить()` содержит ссылку — перед сериализацией конвертировать через `Строка()`. См. helper `ЗначениеДляJSON()` в `docs/1c-http-service-module.md`

**Process для этой кодобазы:**
- После любого фикса в data flow — рестарт `uvicorn` + реальный вопрос в вебе. CI-зелёный ≠ работает в браузере
- Коррекция от пользователя → обновить ВСЕ источники истины сразу: код + spec + README + memory entry. Иначе та же ошибка вернётся
- Untracked `.md` план без коммита = долг. Либо коммитим, либо удаляем

## Modular Docs

See `.claude/rules/` for domain-specific rules:
- `1c-module.md` — 1C BSL code patterns and common errors

@docs/1c-http-service-spec.md
