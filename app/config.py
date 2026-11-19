from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    canvas_base_url: str
    canvas_api_token: str
    demo_mode: bool
    secret_key: str

    @classmethod
    def from_environment(cls) -> Settings:
        project_env = Path(__file__).resolve().parents[1] / ".env"
        load_dotenv(dotenv_path=project_env, override=False)
        return cls(
            canvas_base_url=os.getenv("CANVAS_BASE_URL", ""),
            canvas_api_token=os.getenv("CANVAS_API_TOKEN", ""),
            demo_mode=_as_bool(os.getenv("CANVAS_DEMO_MODE")),
            secret_key=os.getenv("SECRET_KEY", "local-development-key-change-me"),
        )
