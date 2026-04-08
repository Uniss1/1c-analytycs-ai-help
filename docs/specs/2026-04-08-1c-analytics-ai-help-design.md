# 1C Analytics AI Help — Design Spec

**Date:** 2026-04-08
**Status:** Approved

## Problem

Пользователи 1С Аналитики (команда/коллеги) хотят задавать вопросы по данным дашбордов на естественном языке и получать ответы. Два типа вопросов:
- **Data** — "Какая выручка за 1 кв 2025?" → запрос к витринному регистру 1С → ответ с цифрами
- **Knowledge** — "Как считается маржинальность?" → поиск в базе знаний (Wiki.js) → ответ

## Constraints

- LLM: только локальный — Qwen3.5-4B, 4 GPU × 8GB в одной машине (Linux)
- 1С: Linux-сервер (отдельная машина), полный доступ к конфигуратору
- Данные: только витринные регистры для дашбордов (не вся база 1С)
- COM невозможен (Linux) — доступ к данным через HTTP-сервис 1С

## Architecture

```
┌───────────────────────────────────────────────────────┐
│                    ПОЛЬЗОВАТЕЛИ                       │
│  Web-чат (браузер)  │  Виджет в 1С Аналитике (iframe) │
└──────────┬──────────┴──────────┬──────────────────────┘
           │         HTTPS       │
           ▼                     ▼
┌────────────────────────────────────────────────────────┐
│  nginx (reverse proxy)                                 │
│  - /analytics/* → 1С Аналитика + script injection      │
│  - /assistant/api/* → Python API (GPU-сервер)          │
│  - /assistant/widget/* → static files                  │
│  - rate limiting                                       │
└──────────┬─────────────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────┐
│         GPU-СЕРВЕР (Linux, 4×8GB)                      │
│                                                        │
│  FastAPI                                               │
│  ├── Router (GPU 0) — classify: data / knowledge       │
│  ├── Metadata index — SQLite, из 1С Аналитики          │
│  ├── Query generator (GPU 1) + templates               │
│  ├── Query validator — whitelist + sanitize             │
│  ├── Formatter (GPU 2) — data → human answer           │
│  ├── Wiki client (GPU 3) — Wiki.js API → answer        │
│  └── Chat history — SQLite                             │
│                                                        │
│  Ollama × 4 инстанса (по одному на GPU)                │
│  Qwen3.5-4B на каждом                                  │
└──────────┬─────────────────────────────────────────────┘
           │ HTTP (JSON)
           ▼
┌────────────────────────────────────────────────────────┐
│  СЕРВЕР 1С (Linux)                                     │
│  Расширение "AIAssistant"                              │
│  HTTP-сервис: POST /api/v1/query                       │
│  {query, params} → Новый Запрос() → Выполнить() → JSON │
│  Пользователь 1С с ролью "ТолькоЧтениеВитрин"         │
└────────────────────────────────────────────────────────┘
```

## Request Flow

### Data flow:

1. **Router** (GPU 0, ~50 tok) → "data"
2. **Metadata lookup** (Python, no LLM) → find register by keywords + dashboard context
3. **Template match?** → if yes, skip LLM, fill template with params
4. **Query gen** (GPU 1, ~700 tok) → 1C query language
5. **Validate** — whitelist registers, only ВЫБРАТЬ, enforce ПЕРВЫЕ 1000
6. **Execute** → POST to 1C HTTP service → JSON
7. **Format** (GPU 2, ~200 tok) → human answer

### Knowledge flow:

1. **Router** (GPU 0) → "knowledge"
2. **Wiki search** → Wiki.js GraphQL API (pgvector RAG)
3. **Answer** (GPU 3) → answer from wiki context

## LLM Infrastructure

Principle: orchestrate multiple lightweight model instances instead of one heavy model.

- 4 Ollama instances, each pinned to one GPU (ports 11434-11437)
- Same model (Qwen3.5-4B) on all, different system prompts per role
- Parallel execution — router doesn't block formatter

### Optimizations:

- **Context compression** — metadata in compact format, not prose
- **Query templates** — common patterns skip LLM entirely
- **Prefix caching** — system prompts cached in Ollama KV-cache
- **Response caching** — same/similar questions cached with TTL
- **Minimal context per call** — each LLM call gets only what it needs, no history bloat

## Widget (1C Analytics)

Script injection via nginx sub_filter → `widget.js` loads on every 1C Analytics page.

- Floating button → chat panel
- Context-aware: reads current URL/title → sends as dashboard_context
- API calls to /assistant/api/ through same nginx

## Security

Three-layer protection:

1. **Python API** — whitelist registers, only ВЫБРАТЬ, row limit, timeout
2. **1C HTTP service** — dedicated user with read-only role on vitrine registers
3. **nginx** — auth, rate limiting, internal network between servers

## MVP Scope

Included:
- Data queries to vitrine registers
- Knowledge base answers from Wiki.js
- Widget in 1C Analytics
- Standalone web chat
- Dashboard context awareness

Not included:
- Data modification (read-only)
- Access to documents/catalogs (only vitrine registers)
- Report/file generation
- Multi-database support

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Qwen3.5-4B, Ollama (4 instances, 4 GPUs) |
| Backend | Python, FastAPI |
| Metadata | SQLite |
| Chat history | SQLite |
| 1C access | HTTP service (расширение 1С) |
| Knowledge base | Wiki.js (existing ai-wiki project) |
| Web UI | Vanilla HTML/CSS/JS |
| Widget | JS, injected via nginx |
| Reverse proxy | nginx (sub_filter) |
