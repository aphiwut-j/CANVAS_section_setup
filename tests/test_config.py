from __future__ import annotations

import os
from pathlib import Path

import app.config as config_module
from app.config import Settings


def test_settings_load_project_dotenv_without_overriding_process_environment(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}
    monkeypatch.setenv("CANVAS_BASE_URL", "https://process.canvas.example.edu")
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)

    def fake_load_dotenv(*, dotenv_path: Path, override: bool) -> None:
        captured["path"] = dotenv_path
        captured["override"] = override
        os.environ.setdefault("CANVAS_BASE_URL", "https://file.canvas.example.edu")
        os.environ.setdefault("CANVAS_API_TOKEN", "file-token")

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)

    settings = Settings.from_environment()

    assert settings.canvas_base_url == "https://process.canvas.example.edu"
    assert settings.canvas_api_token == "file-token"
    assert Path(captured["path"]).name == ".env"
    assert captured["override"] is False
