"""Metadata index for dashboard registers.

Stores mapping: dashboard → registers → dimensions/resources.
Populated by scripts/sync_metadata.py from 1C Analytics.
"""


def find_register(question: str, dashboard_context: dict | None = None) -> dict | None:
    """Find relevant register by question keywords + dashboard context.

    If dashboard_context provided — search only within that dashboard's registers.
    Otherwise — search all registered vitrine registers.

    Returns register metadata dict or None.
    """
    raise NotImplementedError
