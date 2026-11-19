from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Flask

from app.config import Settings
from app.controllers.web import web
from app.services.backup import BackupService
from app.services.canvas.client import CanvasClient, CanvasError, UnconfiguredCanvasGateway
from app.services.canvas.demo import DemoCanvasGateway
from app.services.operations import BatchPlanStore, OperationService, PlanStore
from app.services.scopes import ScopeImportService, ScopeStore


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    settings = Settings.from_environment()
    app.config.update(
        SECRET_KEY=settings.secret_key,
        CANVAS_BASE_URL=settings.canvas_base_url,
        CANVAS_API_TOKEN=settings.canvas_api_token,
        CANVAS_DEMO_MODE=settings.demo_mode,
        MAX_CONTENT_LENGTH=3 * 1024 * 1024,
        TESTING=False,
    )
    if config:
        app.config.update(config)

    gateway = app.config.get("CANVAS_GATEWAY")
    if gateway is None:
        if app.config["CANVAS_DEMO_MODE"]:
            gateway = DemoCanvasGateway()
        else:
            try:
                gateway = CanvasClient(
                    str(app.config["CANVAS_BASE_URL"]),
                    str(app.config["CANVAS_API_TOKEN"]),
                )
            except CanvasError as exc:
                gateway = UnconfiguredCanvasGateway(str(exc))

    app.extensions["canvas_gateway"] = gateway
    app.extensions["plan_store"] = PlanStore()
    app.extensions["batch_plan_store"] = BatchPlanStore()
    app.extensions["scope_store"] = ScopeStore()
    app.extensions["scope_import_service"] = ScopeImportService(
        gateway, app.extensions["scope_store"]
    )
    app.extensions["operation_service"] = OperationService(
        gateway=gateway,
        backup_service=BackupService(),
        plan_store=app.extensions["plan_store"],
        batch_plan_store=app.extensions["batch_plan_store"],
        scope_store=app.extensions["scope_store"],
    )
    app.register_blueprint(web)
    return app
