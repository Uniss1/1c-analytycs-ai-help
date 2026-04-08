"""Configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM (Ollama)
    ollama_base_url: str = "http://localhost:11434"
    model_name: str = "qwen3.5:4b"

    # 1C HTTP service
    onec_base_url: str = "http://localhost/api/v1"
    onec_user: str = "ai_assistant"
    onec_password: str = ""

    # Wiki.js
    wiki_base_url: str = "http://localhost:3000"
    wiki_api_key: str = ""

    # Limits
    query_timeout: int = 30
    query_row_limit: int = 1000
    rate_limit_per_minute: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
