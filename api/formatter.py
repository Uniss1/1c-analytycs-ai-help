"""Format raw 1C query results into human-readable response."""

import json
from pathlib import Path

from .llm_client import generate

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "formatter.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


async def format_response(
    question: str,
    raw_data: list[dict],
    register_name: str,
) -> tuple[str, dict]:
    """Use LLM (GPU 2) to format raw data into a human answer.

    Input: question + JSON data from 1C.
    Output: (answer, debug_info).
    """
    data_str = json.dumps(raw_data[:50], ensure_ascii=False, default=str)
    prompt = _SYSTEM_PROMPT.replace("{question}", question).replace("{data}", data_str)

    response = await generate(
        role="formatter",
        system_prompt=prompt,
        user_message=question,
    )
    answer = response.strip() if response else "Не удалось сформировать ответ."

    debug_info = {
        "input_data_rows": len(raw_data),
        "input_data_sent": data_str[:2000],
        "raw_llm_response": response,
    }
    return answer, debug_info
