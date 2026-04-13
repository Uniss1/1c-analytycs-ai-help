# tests/test_tool_caller.py
"""Tests for tool_caller — normalization of single query tool params."""

import json

import httpx
import pytest
import respx

from api.tool_caller import (
    MAX_RETRIES,
    _build_example_call,
    _normalize_params,
    call_with_tools,
)


REGISTER_META = {
    "name": "РегистрСведений.Витрина_Дашборда",
    "dimensions": [
        {"name": "Сценарий", "data_type": "Строка", "required": True,
         "default_value": "Факт", "filter_type": "=", "allowed_values": ["Факт", "План"]},
        {"name": "КонтурПоказателя", "data_type": "Строка", "required": True,
         "default_value": "свод", "filter_type": "=", "allowed_values": []},
        {"name": "Показатель", "data_type": "Строка", "required": True,
         "default_value": None, "filter_type": "=", "allowed_values": ["Выручка", "Маржа"]},
        {"name": "ДЗО", "data_type": "Строка", "required": True,
         "default_value": None, "filter_type": "=", "allowed_values": []},
        {"name": "Период_Показателя", "data_type": "Дата", "required": True,
         "default_value": None, "filter_type": "year_month"},
    ],
    "resources": [{"name": "Сумма", "data_type": "Число"}],
}


def test_normalize_aggregate():
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": "Выручка",
        "scenario": "Факт",
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "aggregate"
    assert params["filters"]["Показатель"] == "Выручка"
    assert params["filters"]["Сценарий"] == "Факт"
    assert params["period"] == {"year": 2025, "month": 3}


def test_normalize_aggregate_applies_defaults():
    """Missing scenario → default 'Факт' from metadata."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": "Выручка",
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert params["filters"]["Сценарий"] == "Факт"
    assert params["filters"]["КонтурПоказателя"] == "свод"


def test_normalize_group_by():
    args = {
        "mode": "group_by",
        "resource": "Сумма",
        "metric": "Выручка",
        "group_by": "company",
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "group_by"
    assert params["group_by"] == ["ДЗО"]
    # group_by dimension should NOT appear in filters
    assert "ДЗО" not in params["filters"]


def test_normalize_compare():
    args = {
        "mode": "compare",
        "resource": "Сумма",
        "metric": "Выручка",
        "compare_by": "scenario",
        "compare_values": ["Факт", "План"],
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "compare"
    assert params["compare_by"] == "Сценарий"
    assert params["values"] == ["Факт", "План"]
    # compare_by dimension should NOT appear in filters
    assert "Сценарий" not in params["filters"]


def test_normalize_missing_period_sets_clarification():
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": "Выручка",
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert params["needs_clarification"] is True


def test_normalize_returns_tool_from_mode():
    """Mode becomes the tool name for 1C."""
    args = {"mode": "group_by", "resource": "Сумма", "group_by": "company", "year": 2025, "month": 3}
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "group_by"


# --- Self-healing loop tests -------------------------------------------------

OLLAMA_URL = "http://test-ollama:11434"


def _ok_response(tool_args: dict) -> httpx.Response:
    """Build a successful Ollama /api/chat response with a tool call."""
    return httpx.Response(200, json={
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "query", "arguments": tool_args}}],
        },
    })


def _text_response(content: str) -> httpx.Response:
    """Response with plain text and no tool_calls (forces retry)."""
    return httpx.Response(200, json={
        "message": {"role": "assistant", "content": content},
    })


def test_build_example_call_uses_register_schema():
    """Example includes the first resource and a valid enum value from metadata."""
    example_json = _build_example_call(REGISTER_META)
    example = json.loads(example_json)
    assert example["mode"] == "aggregate"
    assert example["resource"] == "Сумма"
    # Sample should include a value from one of the enum dimensions
    assert any(v in ("Факт", "Выручка") for v in example.values())


@respx.mock
@pytest.mark.asyncio
async def test_call_with_tools_injects_validation_feedback():
    """validation_feedback gets appended as a user message before the API call."""
    route = respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ok_response({
            "mode": "aggregate", "resource": "Сумма",
            "metric": "Выручка", "year": 2025, "month": 3,
        })
    )

    feedback = "ДЗО: 'Газпром' не из допустимых ['ДЗО-1','ДЗО-2']"
    result = await call_with_tools(
        "Выручка по Газпром за март 2025", REGISTER_META,
        base_url=OLLAMA_URL, validation_feedback=feedback,
    )

    assert result["tool"] == "aggregate"
    body = json.loads(route.calls[0].request.content)
    user_msgs = [m["content"] for m in body["messages"] if m["role"] == "user"]
    assert any("Газпром" in m and "Validation errors" not in m or "Газпром" in m for m in user_msgs)
    # Feedback must appear in one of the user messages
    assert any(feedback in m for m in user_msgs)


@respx.mock
@pytest.mark.asyncio
async def test_call_with_tools_retries_no_tool_call_with_example():
    """On text-only response, retry prompt must contain the concrete example."""
    responses = [
        _text_response("Sorry, I'll just chat."),
        _ok_response({
            "mode": "aggregate", "resource": "Сумма",
            "metric": "Выручка", "year": 2025, "month": 3,
        }),
    ]
    route = respx.post(f"{OLLAMA_URL}/api/chat").mock(side_effect=responses)

    result = await call_with_tools(
        "Какая выручка за март 2025?", REGISTER_META, base_url=OLLAMA_URL,
    )

    assert result["tool"] == "aggregate"
    assert route.call_count == 2
    # Second request's last user message should contain the example call
    second_body = json.loads(route.calls[1].request.content)
    last_user = [m for m in second_body["messages"] if m["role"] == "user"][-1]["content"]
    assert "query(" in last_user
    assert "aggregate" in last_user


@respx.mock
@pytest.mark.asyncio
async def test_call_with_tools_exhausts_retries_on_persistent_text():
    """If model never calls a tool, return error after MAX_RETRIES attempts."""
    route = respx.post(f"{OLLAMA_URL}/api/chat").mock(
        side_effect=[_text_response("nope") for _ in range(MAX_RETRIES)]
    )

    result = await call_with_tools(
        "мусорный запрос", REGISTER_META, base_url=OLLAMA_URL,
    )

    assert result.get("tool") is None
    assert route.call_count == MAX_RETRIES
    assert "error" in result


@respx.mock
@pytest.mark.asyncio
async def test_call_with_tools_max_retries_is_four():
    """Regression: MAX_RETRIES was bumped from 2 to 4 for self-healing headroom."""
    assert MAX_RETRIES == 4
