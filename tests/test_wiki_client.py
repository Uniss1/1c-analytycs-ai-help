"""Tests for ai-chat knowledge base client."""

import pytest
import httpx
import respx

from api.wiki_client import ask_knowledge_base


WIKI_URL = "http://localhost:3001"


@respx.mock
@pytest.mark.asyncio
async def test_ask_returns_answer():
    """Question returns answer and sources from ai-chat."""
    respx.post(f"{WIKI_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "answer": "Маржинальность = (Выручка - Себестоимость) / Выручка × 100%",
            "sources": [{"title": "Методология расчёта", "path": "/docs/methodology"}],
            "from_cache": False,
        })
    )

    result = await ask_knowledge_base("Как считается маржинальность?")

    assert "Маржинальность" in result["answer"]
    assert result["from_cache"] is False


@respx.mock
@pytest.mark.asyncio
async def test_ask_not_found():
    """Question on unknown topic returns empty answer."""
    respx.post(f"{WIKI_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "answer": "К сожалению, я не нашёл информации по этому вопросу.",
            "sources": [],
            "from_cache": False,
        })
    )

    result = await ask_knowledge_base("Какая погода на Марсе?")

    assert result["sources"] == []
    assert "не нашёл" in result["answer"]


@respx.mock
@pytest.mark.asyncio
async def test_sources_present():
    """Response contains source links from Wiki.js."""
    respx.post(f"{WIKI_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "answer": "EBITDA — это прибыль до вычета процентов, налогов и амортизации.",
            "sources": [
                {"title": "Глоссарий", "path": "/docs/glossary"},
                {"title": "Финансовые показатели", "path": "/docs/finance"},
            ],
            "from_cache": False,
        })
    )

    result = await ask_knowledge_base("Что такое EBITDA?")

    assert len(result["sources"]) == 2
    assert result["sources"][0]["title"] == "Глоссарий"
    assert result["sources"][1]["path"] == "/docs/finance"


@respx.mock
@pytest.mark.asyncio
async def test_timeout_handling():
    """ai-chat unavailable raises timeout error."""
    respx.post(f"{WIKI_URL}/api/chat").mock(side_effect=httpx.ConnectTimeout("timeout"))

    with pytest.raises(httpx.ConnectTimeout):
        await ask_knowledge_base("Любой вопрос")


@respx.mock
@pytest.mark.asyncio
async def test_history_passed():
    """Chat history is forwarded to ai-chat."""
    route = respx.post(f"{WIKI_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "answer": "Ответ с учётом истории",
            "sources": [],
            "from_cache": False,
        })
    )

    history = [
        {"role": "user", "content": "Что такое маржа?"},
        {"role": "assistant", "content": "Маржа — это разница..."},
    ]
    await ask_knowledge_base("А как она считается?", history=history)

    request_body = route.calls[0].request.content
    import json
    body = json.loads(request_body)
    assert body["history"] == history
    assert body["mode"] == "ai"
