"""Extract structured query parameters from user question via LLM."""

import json
import logging
from pathlib import Path

from .llm_client import generate
from .date_parser import parse_period

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "param_extractor.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def _format_metadata(register_metadata: dict) -> str:
    """Format register metadata for LLM prompt."""
    lines = [f"Регистр: {register_metadata['name']}"]
    if register_metadata.get("description"):
        lines.append(f"Описание: {register_metadata['description']}")
    for dim in register_metadata.get("dimensions", []):
        desc = f" — {dim['description']}" if dim.get("description") else ""
        lines.append(f"Измерение: {dim['name']} ({dim['data_type']}){desc}")
    for res in register_metadata.get("resources", []):
        desc = f" — {res['description']}" if res.get("description") else ""
        lines.append(f"Ресурс: {res['name']} ({res['data_type']}){desc}")
    return "\n".join(lines)


def _build_clarification(params: dict, register_metadata: dict) -> str:
    """Build a clarification question from extracted params."""
    lines = ["Правильно я поняла:"]

    understood = params.get("understood", {})
    desc = understood.get("описание", "")
    if desc:
        lines.append(f"- {desc}")

    # Show what we understood for each filter
    filters = params.get("filters", {})
    for key, val in filters.items():
        if val is not None:
            lines.append(f"- {key}: {val}")

    period = params.get("period", {})
    if period.get("from") and period.get("to"):
        lines.append(f"- Период: {period['from']} — {period['to']}")
    else:
        lines.append("- Период: не указан")

    resource = params.get("resource")
    if resource:
        lines.append(f"- Показатель: {resource}")

    group_by = params.get("group_by", [])
    if group_by:
        lines.append(f"- Группировка: {', '.join(group_by)}")

    lines.append("")
    lines.append("Уточните или подтвердите.")
    return "\n".join(lines)


def _parse_llm_json(response: str) -> dict | None:
    """Parse JSON from LLM response, handling markdown fences."""
    text = response.strip()
    # Remove markdown code fences
    if "```" in text:
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM JSON: %s", text[:200])
        return None


def _apply_date_fallback(params: dict, question: str) -> dict:
    """If LLM didn't extract period, try rule-based date parser."""
    period = params.get("period", {})
    if not period.get("from") or not period.get("to"):
        dates = parse_period(question)
        if dates:
            params["period"] = {
                "from": dates["Начало"],
                "to": dates["Конец"],
            }
    return params


async def extract_params(
    message: str,
    register_metadata: dict,
) -> dict:
    """Extract structured query parameters from user question.

    Returns dict with keys:
        params: dict — extracted parameters (resource, filters, period, group_by, etc.)
        needs_clarification: bool
        clarification_text: str | None — question to ask user
    """
    metadata_text = _format_metadata(register_metadata)
    prompt = _SYSTEM_PROMPT.replace("{metadata}", metadata_text).replace(
        "{question}", message
    )

    response = await generate(role="query", system_prompt=prompt, user_message=message)
    raw_response = response
    params = _parse_llm_json(response)

    if not params:
        return {
            "params": None,
            "needs_clarification": True,
            "clarification_text": "Не удалось разобрать ваш вопрос. Попробуйте переформулировать.",
            "debug": {
                "input_message": message,
                "metadata_sent": metadata_text,
                "raw_llm_response": raw_response,
                "parsed": None,
                "date_fallback": None,
            },
        }

    # Fallback: rule-based date parsing
    before_fallback = {
        "from": (params.get("period") or {}).get("from"),
        "to": (params.get("period") or {}).get("to"),
    }
    params = _apply_date_fallback(params, message)
    after_fallback = {
        "from": (params.get("period") or {}).get("from"),
        "to": (params.get("period") or {}).get("to"),
    }
    date_fallback_applied = before_fallback != after_fallback

    needs_clarification = params.get("needs_clarification", False)
    clarification_text = None
    if needs_clarification:
        clarification_text = _build_clarification(params, register_metadata)

    return {
        "params": params,
        "needs_clarification": needs_clarification,
        "clarification_text": clarification_text,
        "debug": {
            "input_message": message,
            "metadata_sent": metadata_text,
            "raw_llm_response": raw_response,
            "parsed": params,
            "date_fallback_applied": date_fallback_applied,
        },
    }
