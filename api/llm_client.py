"""Ollama LLM client with multi-GPU routing."""

import httpx

from .config import settings

# Each GPU runs its own Ollama instance on a different port
GPU_PORTS = {
    "router": 11434,      # GPU 0
    "query": 11435,        # GPU 1
    "formatter": 11436,    # GPU 2
    "wiki": 11437,         # GPU 3
}


async def generate(role: str, system_prompt: str, user_message: str) -> str:
    """Send request to the appropriate GPU's Ollama instance.

    role: one of 'router', 'query', 'formatter', 'wiki'
    """
    port = GPU_PORTS[role]
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"http://localhost:{port}/api/generate",
            json={
                "model": settings.model_name,
                "system": system_prompt,
                "prompt": user_message,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["response"]
