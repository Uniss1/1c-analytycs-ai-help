"""Configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM (Ollama) — one instance per GPU
    ollama_base_url: str = "http://localhost:11434"
    ollama_router_url: str = ""    # GPU 0, defaults to ollama_base_url
    ollama_query_url: str = ""     # GPU 1, defaults to ollama_base_url:+1
    ollama_formatter_url: str = "" # GPU 2, defaults to ollama_base_url:+2
    model_name: str = "gemma4:e2b"

    # 1C HTTP service
    onec_base_url: str = "http://localhost/base/hs/ai"
    onec_user: str = "ai_assistant"
    onec_password: str = ""

    # ai-chat (knowledge base)
    wiki_base_url: str = "http://localhost:3001"
    wiki_timeout: int = 120

    # OpenAI-compatible API (Ollama / Open WebUI)
    openai_base_url: str = ""
    openai_api_key: str = ""

    # Limits
    query_timeout: int = 30
    query_row_limit: int = 1000
    rate_limit_per_minute: int = 30

    class Config:
        env_file = ".env"

    def gpu_url(self, role: str) -> str:
        """Return Ollama URL for the given role, respecting .env overrides."""
        explicit = {
            "router": self.ollama_router_url,
            "query": self.ollama_query_url,
            "formatter": self.ollama_formatter_url,
        }
        if explicit.get(role):
            return explicit[role]
        # Default: derive from base URL by incrementing port
        offset = {"router": 0, "query": 1, "formatter": 2}.get(role, 0)
        base = self.ollama_base_url.rstrip("/")
        # Split host:port
        parts = base.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return f"{parts[0]}:{int(parts[1]) + offset}"
        return base


settings = Settings()
