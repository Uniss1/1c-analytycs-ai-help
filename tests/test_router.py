"""Tests for intent classification router."""

import pytest
from unittest.mock import AsyncMock, patch

from api.router import classify_intent


@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock)
async def test_data_intent(mock_generate):
    """Data question classified as 'data'."""
    mock_generate.return_value = "data"
    intent, debug = await classify_intent("Какая выручка за март?")
    assert intent == "data"
    assert debug["result"] == "data"


@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock)
async def test_knowledge_intent(mock_generate):
    """Knowledge question classified as 'knowledge'."""
    mock_generate.return_value = "knowledge"
    intent, debug = await classify_intent("Как считается маржинальность?")
    assert intent == "knowledge"


@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock)
async def test_fallback_on_garbage(mock_generate):
    """Unrecognized LLM output falls back to 'data'."""
    mock_generate.return_value = "мусор какой-то"
    intent, debug = await classify_intent("тестовый вопрос")
    assert intent == "data"
    assert debug["raw_llm_response"] == "мусор какой-то"


@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock)
async def test_fallback_on_empty(mock_generate):
    """Empty LLM response falls back to 'data'."""
    mock_generate.return_value = ""
    intent, debug = await classify_intent("тестовый вопрос")
    assert intent == "data"


@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock)
async def test_dashboard_context_passed(mock_generate):
    """Dashboard context included in user message to LLM."""
    mock_generate.return_value = "data"
    intent, debug = await classify_intent("выручка", dashboard_context={"title": "Продажи"})
    assert "Продажи" in debug["input"]
