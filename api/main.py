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
from .onec_client import execute_query, execute_tool
from .param_validator import validate as validate_tool_params
from .router import classify_intent
from .tool_caller import call_with_tools
from .wiki_client import ask_knowledge_base

# Configure logging for all api modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
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
    intent, router_debug = await classify_intent(req.message, req.dashboard_context)
    debug["steps"].append({
        "step": "router",
        "intent": intent,
        "input": router_debug.get("input"),
        "raw_llm_response": router_debug.get("raw_llm_response"),
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
    """Data flow: metadata → tool calling → validate → execute in 1C."""
    # Find register
    t0 = time.monotonic()
    register_meta, meta_debug = find_register(message, dashboard_context)
    ms = int((time.monotonic() - t0) * 1000)
    if not register_meta:
        debug["steps"].append({
            "step": "metadata",
            "found": False,
            "extracted_words": meta_debug.get("extracted_words"),
            "available_keywords": meta_debug.get("available_keywords"),
            "matching_keywords": meta_debug.get("matching_keywords"),
            "ms": ms,
        })
        return {"answer": "Не удалось определить подходящий регистр для вашего вопроса."}

    register_name = register_meta["name"]
    debug["steps"].append({
        "step": "metadata",
        "found": True,
        "register": register_name,
        "extracted_words": meta_debug.get("extracted_words"),
        "matching_keywords": meta_debug.get("matching_keywords"),
        "dimensions": [d["name"] for d in register_meta.get("dimensions", [])],
        "resources": [r["name"] for r in register_meta.get("resources", [])],
        "ms": ms,
    })

    # Tool calling via Gemma
    t0 = time.monotonic()
    tool_result = await call_with_tools(message, register_meta)
    debug["steps"].append({
        "step": "tool_caller",
        "tool": tool_result.get("tool"),
        "args": tool_result.get("args"),
        "error": tool_result.get("error"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    if not tool_result.get("tool"):
        return {
            "answer": "Не удалось обработать вопрос. Попробуйте переформулировать.",
            "register_name": register_name,
        }

    # Normalize result for 1C
    params = tool_result.get("params", {})

    # Check if clarification needed (missing required params)
    if params.get("needs_clarification"):
        _pending_clarifications[session_id] = {
            "params": params,
            "register_metadata": register_meta,
            "tool": tool_result["tool"],
        }
        lines = ["Уточните, пожалуйста:"]
        for dim in register_meta.get("dimensions", []):
            name = dim["name"]
            if not dim.get("required") or dim.get("default_value"):
                continue
            ft = dim.get("filter_type", "=")
            if ft in ("year_month", "range") and not params.get("period", {}).get("year"):
                lines.append(f"- {name}: укажите год и месяц")
            elif ft == "=" and name not in params.get("filters", {}) and name not in params.get("group_by", []):
                allowed = dim.get("allowed_values", [])
                if allowed:
                    lines.append(f"- {name}: выберите из {allowed}")
                else:
                    lines.append(f"- {name}: укажите значение")
        return {
            "answer": "\n".join(lines),
            "register_name": register_name,
            "needs_clarification": True,
        }

    # Validate params before sending to 1C
    validation = validate_tool_params(tool_result, register_meta)
    if not validation.ok:
        debug["steps"].append({"step": "param_validation", "errors": validation.errors})
        return {
            "answer": "Некорректные параметры:\n" + "\n".join(f"- {e}" for e in validation.errors),
            "register_name": register_name,
            "needs_clarification": True,
        }

    # Execute in 1C via new JSON endpoint
    t0 = time.monotonic()
    try:
        onec_result = await execute_tool(tool_result, register_name=register_name)
    except Exception as e:
        logger.error("1C execute_tool failed: %s", e)
        debug["steps"].append({"step": "1c_execute", "error": str(e), "ms": int((time.monotonic() - t0) * 1000)})
        return {
            "answer": f"Ошибка выполнения запроса к 1С: {e}",
            "register_name": register_name,
        }

    exec_ms = int((time.monotonic() - t0) * 1000)

    if not onec_result.get("success"):
        error_type = onec_result.get("error_type", "unknown")
        error_msg = onec_result.get("error_message", "Неизвестная ошибка")
        debug["steps"].append({"step": "1c_execute", "success": False, "error_type": error_type, "error": error_msg, "ms": exec_ms})

        if error_type == "no_data":
            return {"answer": "Данные за указанный период не найдены.", "register_name": register_name}

        return {
            "answer": f"Ошибка: {error_msg}",
            "register_name": register_name,
            "needs_clarification": error_type in ("invalid_params", "missing_params"),
        }

    data = onec_result.get("data", [])
    computed = onec_result.get("computed")
    debug["steps"].append({
        "step": "1c_execute",
        "success": True,
        "rows": len(data),
        "computed": computed,
        "sample": data[:5],
        "ms": exec_ms,
    })

    if not data and not computed:
        return {"answer": "Данные за указанный период не найдены.", "register_name": register_name}

    # Format response
    t0 = time.monotonic()
    format_data = data
    if computed:
        format_data = {"rows": data, "computed": computed}
    answer, fmt_debug = await format_response(message, format_data, register_name)
    debug["steps"].append({
        "step": "formatter",
        "raw_data_rows": len(data),
        "raw_llm_response": fmt_debug.get("raw_llm_response"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    return {
        "answer": answer,
        "register_name": register_name,
        "query_text": onec_result.get("query_text"),
    }


async def _handle_clarification_response(
    message: str,
    session_id: str,
    dashboard_slug: str | None,
    start: float,
    debug: dict,
) -> ChatResponse:
    """Handle user's response to a clarification question."""
    pending = _pending_clarifications.pop(session_id)
    register_meta = pending["register_metadata"]
    register_name = register_meta["name"]

    debug["steps"].append({"step": "clarification_response", "pending_tool": pending.get("tool")})

    # Re-run tool calling with combined context
    original_desc = pending.get("params", {}).get("understood", {}).get("описание", "")
    combined = f"{original_desc}. Уточнение: {message}" if original_desc else message

    t0 = time.monotonic()
    tool_result = await call_with_tools(combined, register_meta)
    debug["steps"].append({
        "step": "tool_caller_retry",
        "tool": tool_result.get("tool"),
        "args": tool_result.get("args"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    if not tool_result.get("tool"):
        latency = int((time.monotonic() - start) * 1000)
        answer = "Не удалось обработать уточнение. Попробуйте задать вопрос заново."
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    # Validate and execute
    validation = validate_tool_params(tool_result, register_meta)
    if not validation.ok:
        latency = int((time.monotonic() - start) * 1000)
        answer = "Некорректные параметры:\n" + "\n".join(f"- {e}" for e in validation.errors)
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name,
            needs_clarification=True, debug=debug,
        )

    t0 = time.monotonic()
    try:
        onec_result = await execute_tool(tool_result, register_name=register_name)
    except Exception as e:
        logger.error("1C execute_tool failed on clarification: %s", e)
        latency = int((time.monotonic() - start) * 1000)
        answer = f"Ошибка выполнения запроса к 1С: {e}"
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    if not onec_result.get("success"):
        latency = int((time.monotonic() - start) * 1000)
        answer = onec_result.get("error_message", "Ошибка выполнения")
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    data = onec_result.get("data", [])
    computed = onec_result.get("computed")

    if not data and not computed:
        answer = "Данные за указанный период не найдены."
    else:
        format_data = data
        if computed:
            format_data = {"rows": data, "computed": computed}
        answer, _ = await format_response(message, format_data, register_name)

    latency = int((time.monotonic() - start) * 1000)
    query_text = onec_result.get("query_text")
    debug["total_ms"] = latency

    save_message(session_id, "user", message)
    save_message(session_id, "assistant", answer,
                 intent="data", register=register_name,
                 query_text=query_text, latency_ms=latency)
    save_cache(message, answer, "data", dashboard_slug)

    return ChatResponse(
        answer=answer, intent="data", session_id=session_id,
        latency_ms=latency, register_name=register_name, debug=debug,
    )


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
