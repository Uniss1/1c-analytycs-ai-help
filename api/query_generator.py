"""Generate 1C query language from user question + metadata."""

import json
from pathlib import Path

from .query_templates import try_match
from .query_validator import validate_query
from .llm_client import generate
from .date_parser import parse_period

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "query_generator.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def _format_metadata(register_metadata: dict) -> str:
    """Format register metadata for LLM prompt."""
    lines = [f"Регистр: {register_metadata['name']}"]
    if register_metadata.get("description"):
        lines.append(f"Описание: {register_metadata['description']}")
    for dim in register_metadata.get("dimensions", []):
        lines.append(f"Измерение: {dim['name']} ({dim['data_type']})")
    for res in register_metadata.get("resources", []):
        lines.append(f"Ресурс: {res['name']} ({res['data_type']})")
    return "\n".join(lines)


def _parse_llm_response(response: str, question: str) -> dict:
    """Extract query and params from LLM text response."""
    query = response.strip()
    # Remove markdown code fences: ```sql ... ``` or ``` ... ```
    if "```" in query:
        lines = query.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        query = "\n".join(lines).strip()

    params = parse_period(question) or {}
    return {"query": query, "params": params}


async def generate_query(
    message: str,
    register_metadata: dict,
    dashboard_context: dict | None = None,
) -> dict:
    """Generate 1C query with parameters.

    First checks query_templates for a match.
    Falls back to LLM generation (GPU 1) if no template fits.

    Returns: {"query": str, "params": dict}
    Raises ValueError if generated query fails validation.
    """
    # 1. Try template match
    result = try_match(message, register_metadata)
    if result:
        return result

    # 2. LLM fallback
    metadata_text = _format_metadata(register_metadata)
    prompt = _SYSTEM_PROMPT.replace("{metadata}", metadata_text).replace(
        "{question}", message
    )
    response = await generate(role="query", system_prompt=prompt, user_message=message)
    result = _parse_llm_response(response, message)

    # 3. Validate
    allowed = {register_metadata["name"]}
    is_valid, error, sanitized = validate_query(result["query"], allowed)
    if not is_valid:
        raise ValueError(f"LLM сгенерировал невалидный запрос: {error}")

    result["query"] = sanitized
    return result
