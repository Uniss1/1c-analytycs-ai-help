"""End-to-end tests for POST /chat endpoint (mocked LLM + 1C + ai-chat)."""

import json
import tempfile
import os

import pytest
import httpx
import respx
from unittest.mock import AsyncMock, patch

from api.config import settings
from api.history import init_history
from api.metadata import init_metadata


@pytest.fixture(autouse=True)
def _setup_dbs(tmp_path):
    """Init metadata and history DBs for each test."""
    # History DB
    history_path = str(tmp_path / "history.db")
    init_history(history_path)

    # Metadata DB with test data
    import sqlite3
    meta_path = str(tmp_path / "metadata.db")
    conn = sqlite3.connect(meta_path)
    conn.executescript("""
        CREATE TABLE dashboards (
            id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT, url_pattern TEXT, updated_at TEXT
        );
        CREATE TABLE registers (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT, register_type TEXT, updated_at TEXT
        );
        CREATE TABLE dashboard_registers (
            dashboard_id INTEGER, register_id INTEGER, widget_title TEXT,
            PRIMARY KEY (dashboard_id, register_id)
        );
        CREATE TABLE dimensions (
            id INTEGER PRIMARY KEY, register_id INTEGER, name TEXT, data_type TEXT, description TEXT,
            required INTEGER NOT NULL DEFAULT 0, default_value TEXT,
            filter_type TEXT NOT NULL DEFAULT '=', allowed_values TEXT
        );
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY, register_id INTEGER, name TEXT, data_type TEXT, description TEXT
        );
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY, register_id INTEGER, keyword TEXT
        );

        INSERT INTO dashboards VALUES (1, 'sales', 'Продажи', '/analytics/sales*', '2025-01-01');
        INSERT INTO registers VALUES (1, 'РегистрНакопления.ВитринаВыручка', 'Выручка', 'accumulation_turnover', '2025-01-01');
        INSERT INTO dashboard_registers VALUES (1, 1, 'Выручка по месяцам');
        INSERT INTO dimensions (id, register_id, name, data_type, required, filter_type, allowed_values)
            VALUES (1, 1, 'Период', 'Дата', 1, 'year_month', NULL);
        INSERT INTO dimensions (id, register_id, name, data_type, required, default_value, filter_type, allowed_values)
            VALUES (2, 1, 'Подразделение', 'Справочник.Подразделения', 0, NULL, '=', NULL);
        INSERT INTO dimensions (id, register_id, name, data_type, required, default_value, filter_type, allowed_values)
            VALUES (3, 1, 'Сценарий', 'Строка', 1, 'Факт', '=', '["Факт", "План"]');
        INSERT INTO resources VALUES (1, 1, 'Сумма', 'Число', NULL);
        INSERT INTO keywords VALUES (1, 1, 'выручка');
        INSERT INTO keywords VALUES (2, 1, 'продажи');
    """)
    conn.commit()
    conn.close()
    init_metadata(meta_path)

    # Patch DB paths in main
    with patch("api.main.METADATA_DB", meta_path), \
         patch("api.main.HISTORY_DB", history_path):
        yield


@pytest.fixture(autouse=True)
def _clear_pending():
    """Clear pending clarifications between tests."""
    from api.main import _pending_clarifications
    _pending_clarifications.clear()
    yield
    _pending_clarifications.clear()


@pytest.fixture()
def client():
    """HTTPX test client for FastAPI app."""
    from api.main import app
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def _sse(*events):
    lines = []
    for event in events:
        lines.append(f"data: {json.dumps(event, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


# --- Data flow: full params (no clarification) ---

@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="data")
@patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=json.dumps({
    "resource": "Сумма",
    "filters": {"Сценарий": "Факт"},
    "period": {"year": 2025, "month": 3},
    "group_by": [],
    "order_by": "desc",
    "limit": 1000,
    "needs_clarification": False,
    "understood": {"описание": "Сумма выручки за март 2025"},
}))
@patch("api.formatter.generate", new_callable=AsyncMock, return_value="Выручка за март: 8.2 млн ₽")
@patch("api.main.execute_query", new_callable=AsyncMock, return_value={
    "success": True, "data": [{"Сумма": 8200000}], "total": 1, "truncated": False,
})
async def test_data_flow_e2e(mock_onec, mock_formatter, mock_extractor, mock_router, client):
    """Data question goes through full pipeline."""
    async with client:
        resp = await client.post("/chat", json={"message": "Какая выручка за март?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "data"
    assert "8.2 млн" in body["answer"]
    assert body["session_id"]
    assert body["latency_ms"] >= 0
    assert not body["needs_clarification"]


# --- Data flow: clarification needed ---

@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="data")
@patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=json.dumps({
    "resource": "Сумма",
    "filters": {"Сценарий": "Факт"},
    "period": {"year": None, "month": None},
    "group_by": ["ДЗО"],
    "order_by": "desc",
    "limit": 1000,
    "needs_clarification": True,
    "understood": {"описание": "Выручка по ДЗО, период не указан"},
}))
async def test_clarification_flow(mock_extractor, mock_router, client):
    """Ambiguous question triggers clarification."""
    async with client:
        resp = await client.post("/chat", json={"message": "выручка по ДЗО"})

    body = resp.json()
    assert body["needs_clarification"]
    assert "Правильно я поняла" in body["answer"]
    assert body["intent"] == "data"


# --- Clarification confirmation ---

@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="data")
@patch("api.formatter.generate", new_callable=AsyncMock, return_value="Выручка: 10 млн")
@patch("api.main.execute_query", new_callable=AsyncMock, return_value={
    "success": True, "data": [{"Сумма": 10000000}], "total": 1, "truncated": False,
})
async def test_clarification_confirm(mock_onec, mock_formatter, mock_router, client):
    """User confirms clarification → query executes."""
    from api.main import _pending_clarifications
    from api.history import create_session

    # Create a real session in the DB so save_message works
    sid = create_session()

    # Simulate pending clarification
    _pending_clarifications[sid] = {
        "params": {
            "resource": "Сумма",
            "filters": {"Сценарий": "Факт"},
            "period": {"year": 2025, "month": 3},
            "group_by": [],
            "order_by": "desc",
            "limit": 1000,
            "understood": {"описание": "Сумма выручки за март"},
        },
        "register_metadata": {
            "name": "РегистрНакопления.ВитринаВыручка",
            "dimensions": [
                {"name": "Период", "data_type": "Дата"},
                {"name": "Сценарий", "data_type": "Строка"},
            ],
            "resources": [{"name": "Сумма", "data_type": "Число"}],
        },
    }

    async with client:
        resp = await client.post("/chat", json={
            "message": "да",
            "session_id": sid,
        })

    body = resp.json()
    assert not body.get("needs_clarification", False)
    assert "10 млн" in body["answer"]
    assert sid not in _pending_clarifications


# --- Knowledge flow (unchanged) ---

@respx.mock
@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="knowledge")
async def test_knowledge_flow_e2e(mock_router, client):
    """Knowledge question routes to ai-chat."""
    wiki_url = settings.wiki_base_url
    respx.post(f"{wiki_url}/api/chat/stream").mock(
        return_value=httpx.Response(200, content=_sse(
            {"type": "sources", "sources": [{"title": "Методология", "path": "/docs/m"}]},
            {"type": "token", "token": "Маржа считается как..."},
            {"type": "done", "from_cache": False},
        ).encode(), headers={"content-type": "text/event-stream"}),
    )

    async with client:
        resp = await client.post("/chat", json={"message": "Как считается маржа?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "knowledge"
    assert "Маржа" in body["answer"]
    assert body["sources"]


# --- Cache ---

@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="data")
@patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=json.dumps({
    "resource": "Сумма",
    "filters": {"Сценарий": "Факт"},
    "period": {"year": 2025, "month": 3},
    "group_by": [],
    "order_by": "desc",
    "limit": 1000,
    "needs_clarification": False,
    "understood": {"описание": "Выручка за март"},
}))
@patch("api.formatter.generate", new_callable=AsyncMock, return_value="Выручка: 10 млн")
@patch("api.main.execute_query", new_callable=AsyncMock, return_value={
    "success": True, "data": [{"Сумма": 10000000}], "total": 1, "truncated": False,
})
async def test_cache_hit(mock_onec, mock_formatter, mock_extractor, mock_router, client):
    """Second identical question returns cached response."""
    async with client:
        resp1 = await client.post("/chat", json={"message": "выручка за март"})
        body1 = resp1.json()

        mock_router.reset_mock()
        mock_extractor.reset_mock()
        mock_formatter.reset_mock()
        mock_onec.reset_mock()

        resp2 = await client.post("/chat", json={
            "message": "выручка за март",
            "session_id": body1["session_id"],
        })
        body2 = resp2.json()

    assert body2["answer"] == body1["answer"]
    mock_router.assert_not_called()


# --- Session history ---

@pytest.mark.asyncio
@patch("api.router.generate", new_callable=AsyncMock, return_value="data")
@patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=json.dumps({
    "resource": "Сумма",
    "filters": {"Сценарий": "Факт"},
    "period": {"year": 2025, "month": 1},
    "group_by": [],
    "order_by": "desc",
    "limit": 1000,
    "needs_clarification": False,
    "understood": {"описание": "Выручка за Q1"},
}))
@patch("api.formatter.generate", new_callable=AsyncMock, return_value="Ответ")
@patch("api.main.execute_query", new_callable=AsyncMock, return_value={
    "success": True, "data": [{"Сумма": 1}], "total": 1, "truncated": False,
})
async def test_session_history(mock_onec, mock_formatter, mock_extractor, mock_router, client):
    """Two questions in same session are both saved in history."""
    from api.history import get_recent_messages

    async with client:
        resp1 = await client.post("/chat", json={"message": "выручка за Q1"})
        sid = resp1.json()["session_id"]

        await client.post("/chat", json={
            "message": "а за Q2?",
            "session_id": sid,
        })

    msgs = get_recent_messages(sid, limit=10)
    assert len(msgs) >= 2
    roles = [m["role"] for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
