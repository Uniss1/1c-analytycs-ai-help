"""ai-chat API client for knowledge base queries.

Uses existing ai-chat service (Uniss1/ai-chat) which provides:
- Hybrid search (vector + keyword + trigram) over Wiki.js
- LLM-generated answers via Ollama
- Source attribution

Uses the streaming endpoint (/api/chat/stream) — same as the Wiki.js
frontend — to ensure complete responses. Collects SSE tokens into a
single answer string.
"""

import json

import httpx

from .config import settings


async def ask_knowledge_base(question: str, history: list[dict] | None = None) -> dict:
    """Ask ai-chat for an answer from the knowledge base.

    Args:
        question: user question in Russian
        history: optional chat history [{role: "user"/"assistant", content: "..."}]

    Returns:
        {answer: str, sources: [{title, path}], from_cache: bool}
    """
    async with httpx.AsyncClient(timeout=settings.wiki_timeout) as client:
        async with client.stream(
            "POST",
            f"{settings.wiki_base_url}/api/chat/stream",
            json={
                "message": question,
                "history": history or [],
                "mode": "ai",
            },
        ) as response:
            response.raise_for_status()

            answer_tokens = []
            sources = []
            from_cache = False

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                event_type = data.get("type")

                if event_type == "sources":
                    sources = data.get("sources", [])
                elif event_type == "token":
                    answer_tokens.append(data.get("token", ""))
                elif event_type == "done":
                    from_cache = data.get("from_cache", False)

            return {
                "answer": "".join(answer_tokens),
                "sources": sources,
                "from_cache": from_cache,
            }
