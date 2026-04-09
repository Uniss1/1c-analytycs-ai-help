"""1C Analytics AI Help — FastAPI entrypoint."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .formatter import format_response
from .history import (
    check_cache,
    create_session,
    get_recent_messages,
    init_history,
    save_cache,
    save_message,
)
from .metadata import find_register, get_all_registers, init_metadata
from .onec_client import execute_query
from .query_generator import generate_query
from .router import classify_intent
from .wiki_client import ask_knowledge_base

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent.parent
METADATA_DB = str(DB_DIR / "metadata.db")
HISTORY_DB = str(DB_DIR / "history.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_metadata(METADATA_DB)
    init_history(HISTORY_DB)
    yield


app = FastAPI(title="1C Analytics AI Help", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/widget", StaticFiles(directory="widget"), name="widget")
app.mount("/web", StaticFiles(directory="web", html=True), name="web")


class ChatRequest(BaseModel):
    message: str
    dashboard_context: dict | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    intent: str
    session_id: str
    latency_ms: int
    sources: list[dict] | None = None
    register: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    start = time.monotonic()

    # Session
    session_id = req.session_id or create_session()

    # Dashboard slug for cache/metadata
    dashboard_slug = None
    if req.dashboard_context and req.dashboard_context.get("url"):
        # Extract slug from URL like /analytics/sales → sales
        parts = req.dashboard_context["url"].rstrip("/").split("/")
        dashboard_slug = parts[-1] if parts else None

    # Cache check
    cached = check_cache(req.message, dashboard_slug)
    if cached:
        latency = int((time.monotonic() - start) * 1000)
        save_message(session_id, "user", req.message)
        save_message(session_id, "assistant", cached["answer"],
                     intent=cached["intent"], latency_ms=latency)
        return ChatResponse(
            answer=cached["answer"],
            intent=cached["intent"],
            session_id=session_id,
            latency_ms=latency,
        )

    # Classify intent
    intent = await classify_intent(req.message, req.dashboard_context)

    answer = ""
    register_name = None
    query_text = None
    sources = None

    if intent == "data":
        answer, register_name, query_text = await _handle_data(
            req.message, req.dashboard_context, dashboard_slug,
        )
    else:
        answer, sources = await _handle_knowledge(req.message, session_id)

    latency = int((time.monotonic() - start) * 1000)

    # Save history
    save_message(session_id, "user", req.message)
    save_message(session_id, "assistant", answer,
                 intent=intent, register=register_name,
                 query_text=query_text, latency_ms=latency)

    # Save cache
    save_cache(req.message, answer, intent, dashboard_slug)

    return ChatResponse(
        answer=answer,
        intent=intent,
        session_id=session_id,
        latency_ms=latency,
        sources=sources,
        register=register_name,
    )


async def _handle_data(
    message: str,
    dashboard_context: dict | None,
    dashboard_slug: str | None,
) -> tuple[str, str | None, str | None]:
    """Data flow: metadata → query → 1C → format."""
    # Find register
    register_meta = find_register(message, dashboard_context)
    if not register_meta:
        return "Не удалось определить подходящий регистр для вашего вопроса.", None, None

    register_name = register_meta["name"]

    # Generate query
    try:
        result = await generate_query(message, register_meta, dashboard_context)
    except ValueError as e:
        return f"Ошибка генерации запроса: {e}", register_name, None

    query_text = result["query"]
    params = result["params"]

    # Execute in 1C
    try:
        onec_result = await execute_query(query_text, params)
    except Exception as e:
        logger.error("1C query failed: %s", e)
        return f"Ошибка выполнения запроса к 1С: {e}", register_name, query_text

    if not onec_result.get("success"):
        error = onec_result.get("error", "Неизвестная ошибка")
        return f"1С вернула ошибку: {error}", register_name, query_text

    data = onec_result.get("data", [])
    if not data:
        return "Данные за указанный период не найдены.", register_name, query_text

    # Format response
    answer = await format_response(message, data, register_name)
    return answer, register_name, query_text


async def _handle_knowledge(
    message: str,
    session_id: str,
) -> tuple[str, list[dict] | None]:
    """Knowledge flow: ai-chat → answer with sources."""
    history = get_recent_messages(session_id, limit=4)

    try:
        result = await ask_knowledge_base(message, history=history)
    except Exception as e:
        logger.error("ai-chat failed: %s", e)
        return f"Ошибка обращения к базе знаний: {e}", None

    return result["answer"], result.get("sources")
