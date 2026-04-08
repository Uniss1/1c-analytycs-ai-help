"""Intent classification: data query vs knowledge base."""


async def classify_intent(message: str, dashboard_context: dict | None = None) -> str:
    """Classify user message as 'data' or 'knowledge'.

    Uses a dedicated LLM instance (GPU 0) with minimal context.
    Returns 'data' or 'knowledge'.
    """
    raise NotImplementedError
