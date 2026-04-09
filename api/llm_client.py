"""Ollama LLM client with multi-GPU routing."""

import httpx

from .config import settings


async def generate(role: str, system_prompt: str, user_message: str) -> str:
    """Send request to the appropriate GPU's Ollama instance.

    role: one of 'router', 'query', 'formatter'
    """
    url = settings.gpu_url(role)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{url}/api/generate",
            json={
                "model": settings.model_name,
                "system": system_prompt,
                "prompt": user_message,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["response"]
