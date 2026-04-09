"""Intent classification: data query vs knowledge base."""

from pathlib import Path

from .llm_client import generate

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "router.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


async def classify_intent(message: str, dashboard_context: dict | None = None) -> tuple[str, dict]:
    """Classify user message as 'data' or 'knowledge'.

    Uses a dedicated LLM instance (GPU 0) with the router prompt.
    Returns (intent, debug_info).
    """
    user_msg = message
    if dashboard_context and dashboard_context.get("title"):
        user_msg = f"[Дашборд: {dashboard_context['title']}] {message}"

    response = await generate(
        role="router",
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_msg,
    )

    raw = response.strip()
    word = raw.split()[0].lower() if raw else "data"
    if word not in ("data", "knowledge"):
        word = "data"

    debug_info = {
        "input": user_msg,
        "raw_llm_response": raw,
        "result": word,
    }
    return word, debug_info
