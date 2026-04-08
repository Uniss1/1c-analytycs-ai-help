"""Generate 1C query language from user question + metadata."""


async def generate_query(
    message: str,
    register_metadata: dict,
    dashboard_context: dict | None = None,
) -> dict:
    """Generate 1C query with parameters.

    First checks query_templates for a match.
    Falls back to LLM generation (GPU 1) if no template fits.

    Returns: {query: str, params: dict}
    """
    raise NotImplementedError
