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
from .param_extractor import extract_params
from .query_builder import build_query
from .query_validator import validate_query
from .router import classify_intent
from .wiki_client import ask_knowledge_base

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent.parent
METADATA_DB = str(DB_DIR / "metadata.db")
HISTORY_DB = str(DB_DIR / "history.db")

# In-memory store for pending clarifications: session_id → {params, register_metadata}
_pending_clarifications: dict[str, dict] = {}


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
    register_name: str | None = None
    needs_clarification: bool = False
    debug: dict | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    start = time.monotonic()
    debug = {"steps": []}

    # Session
    session_id = req.session_id or create_session()
    debug["session_id"] = session_id
    debug["message"] = req.message
    debug["dashboard_context"] = req.dashboard_context

    # Dashboard slug for cache/metadata
    dashboard_slug = None
    if req.dashboard_context and req.dashboard_context.get("url"):
        parts = req.dashboard_context["url"].rstrip("/").split("/")
        dashboard_slug = parts[-1] if parts else None
    debug["dashboard_slug"] = dashboard_slug

    # Check if this is a clarification response
    if session_id in _pending_clarifications:
        return await _handle_clarification_response(
            req.message, session_id, dashboard_slug, start, debug,
        )

    # Cache check
    cached = check_cache(req.message, dashboard_slug)
    if cached:
        debug["steps"].append({"step": "cache", "hit": True, "intent": cached["intent"]})
        latency = int((time.monotonic() - start) * 1000)
        save_message(session_id, "user", req.message)
        save_message(session_id, "assistant", cached["answer"],
                     intent=cached["intent"], latency_ms=latency)
        return ChatResponse(
            answer=cached["answer"],
            intent=cached["intent"],
            session_id=session_id,
            latency_ms=latency,
            debug=debug,
        )

    debug["steps"].append({"step": "cache", "hit": False})

    # Classify intent
    t0 = time.monotonic()
    intent = await classify_intent(req.message, req.dashboard_context)
    debug["steps"].append({
        "step": "router",
        "intent": intent,
        "ms": int((time.monotonic() - t0) * 1000),
    })

    answer = ""
    register_name = None
    query_text = None
    sources = None
    needs_clarification = False

    if intent == "data":
        result = await _handle_data(
            req.message, req.dashboard_context, dashboard_slug, session_id, debug,
        )
        answer = result["answer"]
        register_name = result.get("register_name")
        query_text = result.get("query_text")
        needs_clarification = result.get("needs_clarification", False)
    else:
        answer, sources = await _handle_knowledge(req.message, session_id)
        debug["steps"].append({"step": "knowledge", "sources_count": len(sources) if sources else 0})

    latency = int((time.monotonic() - start) * 1000)
    debug["total_ms"] = latency

    # Save history
    save_message(session_id, "user", req.message)
    save_message(session_id, "assistant", answer,
                 intent=intent, register=register_name,
                 query_text=query_text, latency_ms=latency)

    # Save cache (only for complete answers, not clarifications)
    if not needs_clarification:
        save_cache(req.message, answer, intent, dashboard_slug)

    return ChatResponse(
        answer=answer,
        intent=intent,
        session_id=session_id,
        latency_ms=latency,
        sources=sources,
        register_name=register_name,
        needs_clarification=needs_clarification,
        debug=debug,
    )


async def _handle_data(
    message: str,
    dashboard_context: dict | None,
    dashboard_slug: str | None,
    session_id: str,
    debug: dict,
) -> dict:
    """Data flow: metadata → extract params → clarify or execute."""
    # Find register
    t0 = time.monotonic()
    register_meta = find_register(message, dashboard_context)
    if not register_meta:
        debug["steps"].append({"step": "metadata", "found": False, "ms": int((time.monotonic() - t0) * 1000)})
        return {"answer": "Не удалось определить подходящий регистр для вашего вопроса."}

    register_name = register_meta["name"]
    debug["steps"].append({
        "step": "metadata",
        "found": True,
        "register": register_name,
        "dimensions": [d["name"] for d in register_meta.get("dimensions", [])],
        "resources": [r["name"] for r in register_meta.get("resources", [])],
        "ms": int((time.monotonic() - t0) * 1000),
    })

    # Extract structured params via LLM
    t0 = time.monotonic()
    extraction = await extract_params(message, register_meta)
    debug["steps"].append({
        "step": "param_extractor",
        "needs_clarification": extraction["needs_clarification"],
        "params": extraction["params"],
        "ms": int((time.monotonic() - t0) * 1000),
    })

    if extraction["needs_clarification"]:
        # Store pending state for this session
        _pending_clarifications[session_id] = {
            "params": extraction["params"],
            "register_metadata": register_meta,
        }
        return {
            "answer": extraction["clarification_text"],
            "register_name": register_name,
            "needs_clarification": True,
        }

    # Build and execute query
    return await _execute_query_flow(
        extraction["params"], register_meta, message, debug,
    )


async def _handle_clarification_response(
    message: str,
    session_id: str,
    dashboard_slug: str | None,
    start: float,
    debug: dict,
) -> ChatResponse:
    """Handle user's response to a clarification question."""
    pending = _pending_clarifications.pop(session_id)
    params = pending["params"]
    register_meta = pending["register_metadata"]
    register_name = register_meta["name"]

    debug["steps"].append({"step": "clarification_response", "pending_params": params})

    # Check if user confirms (да, ок, верно, подтверждаю, etc.)
    msg_lower = message.strip().lower()
    confirms = {"да", "ок", "верно", "подтверждаю", "правильно", "точно", "ага", "угу", "yes", "ok"}

    if msg_lower in confirms:
        debug["steps"].append({"step": "user_confirmed", "confirmed": True})
        result = await _execute_query_flow(params, register_meta, message, debug)
    else:
        debug["steps"].append({"step": "user_confirmed", "confirmed": False, "correction": message})
        original_desc = ""
        if params and params.get("understood", {}).get("описание"):
            original_desc = params["understood"]["описание"]

        combined = f"Исходный вопрос: {original_desc}. Уточнение пользователя: {message}"
        t0 = time.monotonic()
        extraction = await extract_params(combined, register_meta)
        debug["steps"].append({
            "step": "param_extractor_retry",
            "needs_clarification": extraction["needs_clarification"],
            "params": extraction["params"],
            "ms": int((time.monotonic() - t0) * 1000),
        })

        if extraction["needs_clarification"]:
            _pending_clarifications[session_id] = {
                "params": extraction["params"],
                "register_metadata": register_meta,
            }
            latency = int((time.monotonic() - start) * 1000)
            debug["total_ms"] = latency
            save_message(session_id, "user", message)
            save_message(session_id, "assistant", extraction["clarification_text"],
                         intent="data", register=register_name, latency_ms=latency)
            return ChatResponse(
                answer=extraction["clarification_text"],
                intent="data",
                session_id=session_id,
                latency_ms=latency,
                register_name=register_name,
                needs_clarification=True,
                debug=debug,
            )

        result = await _execute_query_flow(
            extraction["params"], register_meta, message, debug,
        )

    latency = int((time.monotonic() - start) * 1000)
    debug["total_ms"] = latency
    answer = result["answer"]
    query_text = result.get("query_text")

    save_message(session_id, "user", message)
    save_message(session_id, "assistant", answer,
                 intent="data", register=register_name,
                 query_text=query_text, latency_ms=latency)
    save_cache(message, answer, "data", dashboard_slug)

    return ChatResponse(
        answer=answer,
        intent="data",
        session_id=session_id,
        latency_ms=latency,
        register_name=register_name,
        debug=debug,
    )


async def _execute_query_flow(
    params: dict,
    register_meta: dict,
    message: str,
    debug: dict,
) -> dict:
    """Build query from params, execute in 1C, format response."""
    register_name = register_meta["name"]

    # Build deterministic query
    result = build_query(params, register_meta)
    query_text = result["query"]
    query_params = result["params"]

    debug["steps"].append({
        "step": "query_builder",
        "query": query_text,
        "params": query_params,
    })

    # Validate as safety net
    allowed = {register_name}
    is_valid, error, sanitized = validate_query(query_text, allowed)
    if not is_valid:
        debug["steps"].append({"step": "validator", "valid": False, "error": error})
        return {
            "answer": f"Ошибка построения запроса: {error}",
            "register_name": register_name,
            "query_text": query_text,
        }
    query_text = sanitized
    debug["steps"].append({"step": "validator", "valid": True, "sanitized_query": sanitized})

    # Execute in 1C
    t0 = time.monotonic()
    try:
        onec_result = await execute_query(query_text, query_params)
    except Exception as e:
        logger.error("1C query failed: %s", e)
        debug["steps"].append({"step": "1c_execute", "error": str(e), "ms": int((time.monotonic() - t0) * 1000)})
        return {
            "answer": f"Ошибка выполнения запроса к 1С: {e}",
            "register_name": register_name,
            "query_text": query_text,
        }

    exec_ms = int((time.monotonic() - t0) * 1000)

    if not onec_result.get("success"):
        error = onec_result.get("error", "Неизвестная ошибка")
        debug["steps"].append({"step": "1c_execute", "success": False, "error": error, "ms": exec_ms})
        return {
            "answer": f"1С вернула ошибку: {error}",
            "register_name": register_name,
            "query_text": query_text,
        }

    data = onec_result.get("data", [])
    debug["steps"].append({
        "step": "1c_execute",
        "success": True,
        "rows": len(data),
        "total": onec_result.get("total"),
        "truncated": onec_result.get("truncated"),
        "sample": data[:5],
        "ms": exec_ms,
    })

    if not data:
        return {
            "answer": "Данные за указанный период не найдены.",
            "register_name": register_name,
            "query_text": query_text,
        }

    # Format response
    t0 = time.monotonic()
    answer = await format_response(message, data, register_name)
    debug["steps"].append({
        "step": "formatter",
        "raw_data_rows": len(data),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    return {
        "answer": answer,
        "register_name": register_name,
        "query_text": query_text,
    }


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
