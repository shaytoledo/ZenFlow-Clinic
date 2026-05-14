from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    database_url: str = "sqlite:///./promptbase.db"

    # Which LLM engine powers the optimizer meta-prompt
    # "gemini" = free Google Generative AI tier (default)
    # "anthropic" = Claude (requires paid key)
    optimizer_engine: Literal["gemini", "anthropic"] = "gemini"

    # Keys — only the one matching optimizer_engine is required
    google_api_key: str = ""
    anthropic_api_key: str = ""

    # Gemini model to use — "gemini-1.5-flash" is free-tier friendly
    gemini_model: str = "gemini-1.5-flash"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
