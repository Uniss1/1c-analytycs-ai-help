"""Chat history storage (SQLite)."""


def save_message(session_id: str, role: str, content: str) -> None:
    """Save a message to chat history."""
    raise NotImplementedError


def get_recent_messages(session_id: str, limit: int = 4) -> list[dict]:
    """Get last N messages for context. Returns compressed summary + recent messages."""
    raise NotImplementedError
