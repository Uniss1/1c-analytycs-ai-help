"""Tests for ai-chat knowledge base client (SSE streaming)."""

import pytest
import httpx
import respx

from api.wiki_client import ask_knowledge_base


WIKI_URL = "http://localhost:3001"


def _sse(*events: dict) -> str:
    """Build SSE response body from event dicts."""
    lines = []
    for event in events:
        lines.append(f"data: {__import__('json').dumps(event, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def _mock_stream(route, *events):
    """Mock a streaming SSE response."""
    body = _sse(*events)
    route.mock(return_value=httpx.Response(
        200,
        content=body.encode(),
        headers={"content-type": "text/event-stream"},
    ))


@respx.mock
@pytest.mark.asyncio
async def test_ask_returns_answer():
    """Question returns full answer assembled from SSE tokens."""
    route = respx.post(f"{WIKI_URL}/api/chat/stream")
    _mock_stream(route,
        {"type": "sources", "sources": [{"title": "Методология", "path": "/docs/method"}]},
        {"type": "token", "token": "Маржинальность"},
        {"type": "token", "token": " = (Выручка - Себестоимость)"},
        {"type": "token", "token": " / Выручка × 100%"},
        {"type": "done", "from_cache": False},
    )

    result = await ask_knowledge_base("Как считается маржинальность?")

    assert result["answer"] == "Маржинальность = (Выручка - Себестоимость) / Выручка × 100%"
    assert result["from_cache"] is False
    assert len(result["sources"]) == 1


@respx.mock
@pytest.mark.asyncio
async def test_ask_not_found():
    """Question on unknown topic returns empty answer."""
    route = respx.post(f"{WIKI_URL}/api/chat/stream")
    _mock_stream(route,
        {"type": "sources", "sources": []},
        {"type": "token", "token": "К сожалению, я не нашёл информации по этому вопросу."},
        {"type": "done", "from_cache": False},
    )

    result = await ask_knowledge_base("Какая погода на Марсе?")

    assert result["sources"] == []
    assert "не нашёл" in result["answer"]


@respx.mock
@pytest.mark.asyncio
async def test_sources_present():
    """Response contains source links from Wiki.js."""
    route = respx.post(f"{WIKI_URL}/api/chat/stream")
    _mock_stream(route,
        {"type": "sources", "sources": [
            {"title": "Глоссарий", "path": "/docs/glossary"},
            {"title": "Финансовые показатели", "path": "/docs/finance"},
        ]},
        {"type": "token", "token": "EBITDA — прибыль до вычета."},
        {"type": "done", "from_cache": False},
    )

    result = await ask_knowledge_base("Что такое EBITDA?")

    assert len(result["sources"]) == 2
    assert result["sources"][0]["title"] == "Глоссарий"
    assert result["sources"][1]["path"] == "/docs/finance"


@respx.mock
@pytest.mark.asyncio
async def test_timeout_handling():
    """ai-chat unavailable raises timeout error."""
    respx.post(f"{WIKI_URL}/api/chat/stream").mock(
        side_effect=httpx.ConnectTimeout("timeout"),
    )

    with pytest.raises(httpx.ConnectTimeout):
        await ask_knowledge_base("Любой вопрос")


@respx.mock
@pytest.mark.asyncio
async def test_history_passed():
    """Chat history is forwarded to ai-chat."""
    route = respx.post(f"{WIKI_URL}/api/chat/stream")
    _mock_stream(route,
        {"type": "sources", "sources": []},
        {"type": "token", "token": "Ответ"},
        {"type": "done", "from_cache": False},
    )

    history = [
        {"role": "user", "content": "Что такое маржа?"},
        {"role": "assistant", "content": "Маржа — это разница..."},
    ]
    await ask_knowledge_base("А как она считается?", history=history)

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["history"] == history
    assert body["mode"] == "ai"


@respx.mock
@pytest.mark.asyncio
async def test_cached_response():
    """Cached response has from_cache=True."""
    route = respx.post(f"{WIKI_URL}/api/chat/stream")
    _mock_stream(route,
        {"type": "sources", "sources": [{"title": "Кэш", "path": "/cached"}]},
        {"type": "token", "token": "Ответ из кэша"},
        {"type": "done", "from_cache": True},
    )

    result = await ask_knowledge_base("Закэшированный вопрос")

    assert result["from_cache"] is True
    assert result["answer"] == "Ответ из кэша"
