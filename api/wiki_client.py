"""Wiki.js API client for knowledge base queries."""


async def search_wiki(query: str) -> list[dict]:
    """Search Wiki.js via GraphQL API with pgvector RAG.

    Returns list of relevant article chunks.
    """
    raise NotImplementedError


async def answer_from_wiki(question: str, context_chunks: list[dict]) -> str:
    """Use LLM (GPU 3) to answer based on wiki context.

    Returns natural language answer in Russian.
    """
    raise NotImplementedError
