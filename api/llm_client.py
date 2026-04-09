"""Ollama LLM client with multi-GPU routing."""

import asyncio
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds — wait for model to load into VRAM


async def generate(role: str, system_prompt: str, user_message: str) -> str:
    """Send request to the appropriate GPU's Ollama instance.

    role: one of 'router', 'query', 'formatter'
    Retries once if Ollama returns done_reason=load (model loading).
    """
    url = settings.gpu_url(role)
    payload = {
        "model": settings.model_name,
        "system": system_prompt,
        "prompt": user_message,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1},
    }

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            response = await client.post(f"{url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("done_reason") == "load" and attempt < MAX_RETRIES:
                logger.warning(
                    "Ollama model loading (attempt %d/%d), retrying in %ds...",
                    attempt, MAX_RETRIES, RETRY_DELAY,
                )
                await asyncio.sleep(RETRY_DELAY)
                continue

            text = data.get("response", "")
            if text:
                return text

            logger.warning(
                "Ollama returned empty response: %s", data.get("done_reason")
            )

    return ""
