"""Single place for every setting and secret.

Two ideas worth noticing:
  1. Secrets are read from the environment (.env), never written in code,
     because code goes to GitHub and secrets must not.
  2. Every setting has a fallback. No MySQL? Use SQLite. No API key?
     The app still starts and every financial number still works.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- database -------------------------------------------------------
    # Falls back to a local SQLite file so the project runs with zero setup.
    DATABASE_URL: str = os.getenv("DATABASE_URL") or "sqlite:///./sahyogai.db"

    # --- llm ------------------------------------------------------------
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"

    # --- vector store ---------------------------------------------------
    CHROMA_DIR: str = os.getenv("CHROMA_DIR") or "./chroma_store"

    # --- twilio (whatsapp channel only) ---------------------------------
    TWILIO_ACCOUNT_SID: str | None = os.getenv("TWILIO_ACCOUNT_SID") or None
    TWILIO_AUTH_TOKEN: str | None = os.getenv("TWILIO_AUTH_TOKEN") or None
    TWILIO_WHATSAPP_FROM: str | None = os.getenv("TWILIO_WHATSAPP_FROM") or None

    @property
    def llm_enabled(self) -> bool:
        """One flag the whole app checks: do we have AI available right now?"""
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def using_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


settings = Settings()
