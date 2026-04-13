---
status: approved
date: 2026-04-13
---

# Ретроспектива 4 дней работы → Lessons в CLAUDE.md + raw для Obsidian

## Цель

Извлечь из 30+ коммитов и 6 jsonl-сессий за 2026-04-10…04-13 повторяющиеся
ошибки и удачные паттерны, и положить их в два места:

1. **Проектный `CLAUDE.md`** — actionable правила, чтобы Claude в этом
   репозитории не повторял ту же работу заново.
2. **`obsidian-llm-wiki/raw/`** — сырьё для curator-Claude vault'а, который
   разнесёт concepts по wiki-страницам и переиспользует в будущих
   LLM-проектах.

Глобальный `~/.claude/CLAUDE.md` явно не трогаем (решение пользователя).

## Источники

- `git log --oneline -40`
- `docs/decisions/2026-04-12-self-healing.md`
- `docs/superpowers/specs/2026-04-10-smart-1c-backend-design.md`
- `docs/plans/2026-04-11-single-query-tool-design.md`
- `docs/superpowers/plans/*` (4 файла)
- 6 jsonl-сессий из `~/.claude/projects/-home-dmin-projects-1c-analytics-ai-help/`
  (саммари получено через subagent)
- 3 memory-записи (URL templates, model config, ollama proxy)

## Артефакт 1 — раздел в `CLAUDE.md`

**Где:** в конце файла, после `## Known Issues / TODO`, перед `## Modular Docs`.

**Размер:** не более 30 строк (текущий файл 131, лимит 200).

**Структура:**

```markdown
## Lessons learned

Уроки 4 дней постройки. Каждое правило — императив + 1 строка «почему».

**SLM tool calling (5B-класс):**
- Один tool с `mode` enum > 7 разных tools — модель путается в выборе имени
- Латинские ключи в JSON Schema > кириллические — токенизация ломает small models
- Enum-значения и дефолты в schema И в system message — двойное подкрепление
- Перед бенчмарком — `cat .env | grep MODEL_NAME`, не доверять докам

**Архитектурные:**
- Текст 1С-запроса не пересекает сеть. Только JSON params
- Правило в двух местах → выноси helper. Technical-dim фикс ловили дважды
- Не ставить LLM туда, где работает шаблон. Каждый LLM-вызов = +1–3 сек
- Чинить root cause, не симптом

**1С платформенные ограничения:**
- URL-шаблоны односегментные: `/analytics_execute`, не `/analytics/execute`
- РегистрСведений имеет `Измерения` + `Ресурсы` + `Реквизиты` — проверять обе
- Оператор сравнения через switch/case, не конкатенацией строки

**Process для этой кодобазы:**
- После любого фикса в data flow — рестарт `uvicorn` + реальный вопрос в вебе
- Коррекция от пользователя → обновить ВСЕ источники истины сразу
- Untracked `.md` план без коммита = долг, либо коммитим, либо удаляем
```

## Артефакт 2 — raw-файл для Obsidian wiki

**Где:** `/mnt/c/Users/Admin/Projects/obsidian-llm-wiki/raw/2026-04-13-1c-analytics-slm-tool-calling-retrospective.md`

**Конвенции vault'а** (из его CLAUDE.md):
- kebab-case имя файла, английский, дата-префикс — соответствует
- Тело: русский, технические термины на английском
- Подсказки `[[wikilinks]]` curator'у в финальной секции

**Структура** (одна сессия = один файл, под INGEST-операцию curator'а):

```markdown
# 1C Analytics AI Help — ретроспектива (4 дня, 2026-04-10…04-13)

> Источник: проект /home/dmin/projects/1c-analytics-ai-help.
> Стек: SLM tool calling (qwen3.5:4b / gemma4:e2b) через Ollama,
> FastAPI backend, 1С HTTP-сервис на BSL.

## Контекст
- Цель: AI-помощник для дашбордов 1С Аналитики
- 30+ коммитов за 4 дня, 6 крупных переделок
- К концу: -1736 / +149 в одном cleanup-коммите

## SLM tool calling — что работает
[5–8 паттернов: single-tool-mode, latin keys, self-healing, metadata-driven schema,
Ollama native API, tool_choice=required, dynamic enums, …]

## SLM tool calling — что не работает
[3–5 анти-паттернов: 7 tool names на 5B, /v1 endpoint для Gemma, LLM роутер
для bin-classification, hardcoded skip-lists без fallback]

## Архитектурные паттерны
- Move-to-platform: query construction в 1С, не в Python
- Template formatter > LLM formatter
- Helper для cross-cutting concerns

## 1С HTTP-сервис gotchas
- URL templates односегментные
- РегистрСведений: 3 коллекции метаданных
- BSL reserved words (Знач, Строка)
- Switch/case для операторов вместо конкатенации

## Process anti-patterns (работа с Claude Code)
- Claimed success без end-to-end verification
- Scope creep fix → refactor
- Премaturные commits на foundational choices
- Infrastructure yak-shaving в начале сессий
- Repeat correction: правило приходится повторять 3×, потому что не обновили
  все источники истины (код + spec + README) сразу

## Insights
- Self-healing с validation feedback дешёвый и работает
- Move-logic-to-platform убирает целые классы багов
- Metadata interview (sync_metadata) > hardcoded списки
- Cleanup-pass даёт ratio -10×: «чем дальше, тем больше мёртвого кода»

## Концепции для wiki (curator hints)
- [[slm-tool-calling]]
- [[ollama-native-api-vs-openai-compat]]
- [[json-schema-design-for-small-models]]
- [[self-healing-llm-loop]]
- [[1c-http-service-design]]
- [[move-logic-to-platform]]
- [[metadata-driven-tool-schema]]
- [[claude-code-anti-patterns]]
- [[template-formatter-vs-llm-formatter]]
```

**Длина:** 400–600 строк (плотный текст с примерами/коммитами).

## Acceptance criteria

- `wc -l CLAUDE.md` ≤ 200
- Раздел Lessons содержит 11–14 правил, не размытых, каждое actionable
- Каждое правило связано с реальным инцидентом из git log / памяти
  (никаких выдуманных best practices)
- Raw-файл создан, frontmatter не нужен (по конвенции vault'а у raw нет
  frontmatter — это сырьё)
- Раздел `## Концепции для wiki` содержит ≥7 wikilink-подсказок с
  kebab-case именами на английском
- Изменения CLAUDE.md и новый spec закоммичены; raw-файл не коммитится
  (он живёт в другом git-репо vault'а)

## Что НЕ делаем

- Не трогаем `~/.claude/CLAUDE.md` (глобальный)
- Не трогаем существующие memory-записи (они остаются как есть)
- Не реструктурируем существующий CLAUDE.md (только добавляем секцию)
- Не пишем сами в `wiki/` vault'а — это работа curator-Claude через INGEST

## План реализации (передаётся в writing-plans)

1. Добавить блок `## Lessons learned` в `CLAUDE.md` после `## Known Issues / TODO`
2. Создать raw-файл по пути выше с полным содержимым
3. Закоммитить spec + CLAUDE.md одним коммитом
4. Пуш origin/main по подтверждению пользователя
