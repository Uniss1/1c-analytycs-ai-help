"""Gemma 4 E2B tool calling via Ollama native API.

Sends user question with tool definitions to Ollama /api/chat,
parses tool_calls response.
Returns structured params compatible with param_validator + 1C HTTP service.
"""

import json
import logging
import re

import httpx

from .config import settings
from .filter_utils import as_string_list
from .tool_defs import build_system_message, build_tools, is_technical_dim, key_to_dim

logger = logging.getLogger(__name__)

MAX_RETRIES = 4

VALID_TOOLS = {"query"}


def _build_example_call(register_metadata: dict) -> str:
    """Build a concrete example tool call from register schema for prompt reinforcement."""
    resources = register_metadata.get("resources", [])
    resource = resources[0]["name"] if resources else "Сумма"
    example: dict = {"mode": "aggregate", "resource": resource, "year": 2026, "month": 3}
    for dim in register_metadata.get("dimensions", []):
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if is_technical_dim(dim):
            continue
        allowed = dim.get("allowed_values") or []
        if not allowed:
            continue
        from .tool_defs import _dim_key
        example[_dim_key(dim["name"])] = allowed[0]
        break
    return json.dumps(example, ensure_ascii=False)


async def call_with_tools(
    question: str,
    register_metadata: dict,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    base_url: str | None = None,
    api_key: str | None = None,
    validation_feedback: str | None = None,
) -> dict:
    """Call model via Ollama /api/chat with tools.

    Returns:
        {
            "tool": str — selected tool name,
            "args": dict — tool arguments filled by model,
            "params": dict — normalized params for 1C HTTP service,
            "raw_response": dict — full API response for debugging,
        }
        Or on failure:
        {
            "tool": None,
            "error": str,
            "raw_response": dict | str,
        }
    """
    url = base_url or settings.ollama_base_url or "http://localhost:11434"
    tools = build_tools(register_metadata)
    system_msg = build_system_message(register_metadata)
    model_name = model or settings.model_name

    messages: list[dict] = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": question},
    ]
    if validation_feedback:
        messages.append({
            "role": "user",
            "content": (
                f"{validation_feedback}\n\n"
                "Re-emit the 'query' tool call with corrected parameters. "
                "Copy enum values EXACTLY as spelled in the tool schema — "
                "do NOT translate, lowercase, or paraphrase."
            ),
        })

    example_call = _build_example_call(register_metadata)

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            # Build payload for Ollama native API
            payload = {
                "model": model_name,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": temperature},
            }

            try:
                response = await client.post(
                    f"{url.rstrip('/')}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Ollama HTTP error (attempt %d): %s", attempt, e)
                if attempt < MAX_RETRIES:
                    continue
                return {"tool": None, "error": str(e), "raw_response": ""}

            data = response.json()
            # Log raw response for debugging
            msg = data.get("message", {})
            logger.info("OLLAMA raw: tool_calls=%s, content=%s",
                        msg.get("tool_calls", "NONE"),
                        (msg.get("content", "") or "")[:200])
            parsed = _parse_ollama_response(data, register_metadata)

            if parsed.get("tool") is not None and "error" not in parsed:
                return parsed

            # No valid tool call — retry with reinforcement
            if attempt < MAX_RETRIES:
                logger.warning("No tool call on attempt %d, retrying", attempt)
                messages.append({
                    "role": "assistant",
                    "content": parsed.get("error", ""),
                })
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response had no tool_calls. "
                        f"Call the 'query' tool now with JSON arguments. "
                        f"Example: query({example_call})"
                    ),
                })
                continue

            return parsed

    return {"tool": None, "error": "Max retries exceeded", "raw_response": ""}


def _parse_ollama_response(data: dict, register_metadata: dict) -> dict:
    """Parse Ollama native /api/chat response.

    Ollama format:
    {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "aggregate", "arguments": {...}}}
            ]
        }
    }
    """
    message = data.get("message", {})
    tool_calls = message.get("tool_calls", [])

    if not tool_calls:
        content = message.get("content", "")
        logger.warning("No tool_calls in Ollama response. Content: %s", content[:300])

        # Fallback: try to parse tool call from text content
        parsed = _try_parse_content_as_tool_call(content)
        if parsed:
            logger.info("Parsed tool call from content: %s", parsed.get("name"))
            tool_calls = [{"function": parsed}]
        else:
            return {
                "tool": None,
                "error": f"Model responded with text instead of tool call: {content[:300]}",
                "raw_response": data,
            }

    # Take the first tool call
    tc = tool_calls[0]
    func = tc.get("function", {})
    tool_name = func.get("name", "")
    arguments = func.get("arguments", {})

    # Parse arguments if string
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {
                "tool": tool_name,
                "error": f"Failed to parse arguments JSON: {arguments[:200]}",
                "raw_response": data,
            }

    # Validate tool name — must be "query"
    if tool_name not in VALID_TOOLS:
        logger.warning("Invalid tool name '%s', trying to extract from arguments", tool_name)
        if isinstance(arguments, dict):
            inner_name = arguments.get("name", arguments.get("tool", ""))
            if inner_name in VALID_TOOLS:
                tool_name = inner_name
                arguments = arguments.get("arguments", arguments.get("parameters",
                    {k: v for k, v in arguments.items() if k not in ("name", "tool")}))
            else:
                return {
                    "tool": None,
                    "error": f"Неизвестный инструмент: {func.get('name', '')}",
                    "raw_response": data,
                }

    mode, params = _normalize_params(arguments, register_metadata)

    return {
        "tool": mode,
        "args": arguments,
        "params": params,
        "raw_response": data,
    }


def _try_parse_content_as_tool_call(content: str) -> dict | None:
    """Try to extract a tool call from model's text content."""
    if not content:
        return None
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            name = data.get("name", "")
            if name in VALID_TOOLS:
                return {"name": name, "arguments": data.get("arguments", data.get("parameters", {}))}
            tool = data.get("tool", "")
            if tool in VALID_TOOLS:
                args = {k: v for k, v in data.items() if k != "tool"}
                return {"name": tool, "arguments": args}
    except (json.JSONDecodeError, TypeError):
        pass

    json_match = re.search(r'\{[^{}]*"(?:name|tool)"[^{}]*\}', content)
    if json_match:
        try:
            data = json.loads(json_match.group())
            name = data.get("name", data.get("tool", ""))
            if name in VALID_TOOLS:
                args = data.get("arguments", data.get("parameters", {}))
                if not isinstance(args, dict):
                    args = {k: v for k, v in data.items() if k not in ("name", "tool")}
                return {"name": name, "arguments": args}
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _normalize_params(args: dict, register_metadata: dict) -> tuple[str, dict]:
    """Convert single query tool arguments to 1C HTTP service format.

    Args use Latin keys (metric, scenario, company, year, month) + mode.
    Filter values are always arrays in the 1C payload. Strings are wrapped
    in single-element arrays; empty arrays are dropped so that defaults can
    apply.

    Returns (tool_name, params) where tool_name is the mode value
    and params use 1C names (Показатель, Сценарий, ДЗО) + period dict.
    """
    mode = args.get("mode", "aggregate")
    resource = args.get("resource", "Сумма")
    year = args.get("year")
    month = args.get("month")
    group_by_latin = args.get("group_by")
    order_by = args.get("order", args.get("order_by", "desc"))
    limit = args.get("limit", 1000)

    # Period: month is optional; absence == whole year
    period: dict = {}
    if year is not None:
        period["year"] = year
        if month is not None:
            period["month"] = month

    compare_by_cyrillic = key_to_dim(args.get("compare_by", "")) if mode == "compare" else ""
    group_by_cyrillic = key_to_dim(group_by_latin) if group_by_latin else ""

    skip_keys = {
        "mode", "resource", "year", "month", "group_by", "order", "order_by",
        "limit", "compare_by", "compare_values",
    }

    filters: dict = {}
    for k, v in args.items():
        if k in skip_keys:
            continue
        dim_name = key_to_dim(k)
        if dim_name == group_by_cyrillic:
            continue
        if dim_name == compare_by_cyrillic:
            continue
        coerced = [x for x in as_string_list(v) if x != ""]
        if not coerced:
            continue
        filters[dim_name] = coerced

    # Apply defaults for dimensions not provided (defaults go in as arrays too)
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if name == group_by_cyrillic or name == compare_by_cyrillic:
            continue
        if name not in filters and dim.get("default_value"):
            filters[name] = [str(dim["default_value"])]

    group_by: list = []
    if group_by_latin:
        group_by = [group_by_cyrillic]

    extra: dict = {}
    if mode == "compare":
        extra["compare_by"] = compare_by_cyrillic
        extra["values"] = args.get("compare_values", [])

    # needs_clarification: only year matters for period (month is optional)
    needs_clarification = False
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if is_technical_dim(dim):
            continue
        if not dim.get("required"):
            continue
        if dim.get("default_value"):
            continue
        ft = dim.get("filter_type", "=")
        if ft in ("year_month", "range"):
            if not period.get("year"):
                needs_clarification = True
                break
        elif ft == "=":
            if mode == "compare" and name == compare_by_cyrillic:
                continue
            if name in group_by:
                continue
            if name not in filters:
                needs_clarification = True
                break

    result = {
        "resource": resource,
        "filters": filters,
        "period": period,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
        "needs_clarification": needs_clarification,
    }
    result.update(extra)
    return mode, result
