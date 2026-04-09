"""Extract structured query parameters from user question via LLM."""

import json
import logging
from pathlib import Path

from .llm_client import generate

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "param_extractor.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def _format_metadata(register_metadata: dict) -> str:
    """Format register metadata for LLM prompt, showing allowed values and defaults."""
    lines = [f"Регистр: {register_metadata['name']}"]
    if register_metadata.get("description"):
        lines.append(f"Описание: {register_metadata['description']}")

    lines.append("")
    lines.append("Измерения:")
    for dim in register_metadata.get("dimensions", []):
        required = dim.get("required", False)
        req_label = "обязательное" if required else "необязательное"
        default = dim.get("default_value")
        filter_type = dim.get("filter_type", "=")
        allowed = dim.get("allowed_values", [])

        parts = [f"  {dim['name']} ({dim['data_type']}) — {req_label}"]
        if filter_type == "year_month":
            parts.append(f"    фильтр: ГОД/МЕСЯЦ")
        elif filter_type == "range":
            parts.append(f"    фильтр: диапазон (от/до)")
        if default:
            parts.append(f"    по умолчанию: {default}")
        if allowed:
            parts.append(f"    допустимые значения: {', '.join(str(v) for v in allowed)}")
        if dim.get("description"):
            parts.append(f"    описание: {dim['description']}")
        lines.extend(parts)

    lines.append("")
    lines.append("Ресурсы:")
    for res in register_metadata.get("resources", []):
        desc = f" — {res['description']}" if res.get("description") else ""
        lines.append(f"  {res['name']} ({res['data_type']}){desc}")

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
    year = period.get("year")
    month = period.get("month")
    if year and month:
        lines.append(f"- Период: {month}.{year}")
    else:
        lines.append("- Период: не указан")

    resource = params.get("resource")
    if resource:
        lines.append(f"- Показатель: {resource}")

    group_by = params.get("group_by", [])
    if group_by:
        lines.append(f"- Группировка: {', '.join(group_by)}")

    # Show what's missing with allowed values
    missing = []
    for dim in register_metadata.get("dimensions", []):
        dim_name = dim["name"]
        required = dim.get("required", False)
        default = dim.get("default_value")
        filter_type = dim.get("filter_type", "=")

        if not required or default:
            continue

        if filter_type == "year_month":
            if not (year and month):
                missing.append(f"- {dim_name}: укажите год и месяц")
        elif filter_type == "=":
            val = filters.get(dim_name)
            if val is None:
                allowed = dim.get("allowed_values", [])
                if allowed:
                    missing.append(f"- {dim_name}: выберите из [{', '.join(str(v) for v in allowed)}]")
                else:
                    missing.append(f"- {dim_name}: укажите значение")

    if missing:
        lines.append("")
        lines.append("Не хватает:")
        lines.extend(missing)

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
            },
        }

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
        },
    }
