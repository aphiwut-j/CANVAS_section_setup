from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.services.backup import BackupError
from app.services.canvas.client import CanvasError
from app.services.operations import OperationService, ValidationError
from app.services.scopes import ScopeError, ScopeImportService
from app.services.sis_scopes import SisScopeImportService

web = Blueprint("web", __name__)


def _service() -> OperationService:
    return current_app.extensions["operation_service"]  # type: ignore[no-any-return]


def _scope_importer() -> ScopeImportService:
    return current_app.extensions["scope_import_service"]  # type: ignore[no-any-return]


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValidationError("A JSON request body is required.")
    return value


def _require_browser_request() -> None:
    if request.headers.get("X-Requested-With") != "CanvasSectionPlanner":
        raise ValidationError("The operation must be submitted from this web application.")


@web.get("/")
def index() -> str:
    return render_template("index.html", demo_mode=bool(current_app.config["CANVAS_DEMO_MODE"]))


@web.get("/health")
def health() -> tuple[Any, int]:
    return jsonify({"status": "ok"}), 200


@web.get("/favicon.ico")
def favicon() -> tuple[str, int]:
    return "", 204


@web.get("/api/status")
def status() -> tuple[Any, int]:
    gateway = current_app.extensions["canvas_gateway"]
    return (
        jsonify(
            {
                "demo_mode": bool(current_app.config["CANVAS_DEMO_MODE"]),
                "canvas_host": gateway.host_identifier,
                "configured": gateway.host_identifier != "not-configured",
            }
        ),
        200,
    )


@web.post("/api/scopes")
def import_scope() -> tuple[Any, int]:
    _require_browser_request()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise ScopeError("Choose a CSV or Excel file.")
    data = uploaded.stream.read(ScopeImportService.MAX_FILE_BYTES + 1)
    mode = request.form.get("import_mode", "standard")
    if mode == "sis_dates":
        importer = _scope_importer()
        scope = SisScopeImportService(importer.gateway, importer.store).import_scope(
            uploaded.filename,
            data,
            reassessment_only=request.form.get("reassessment_only", "true") != "false",
        )
    elif mode == "standard":
        scope = _scope_importer().import_scope(uploaded.filename, data)
    else:
        raise ScopeError("Select a supported import option.")
    return jsonify({"scope": scope.to_dict()}), 201


@web.get("/api/scopes/<scope_id>/courses/<course_id>/workspace")
def workspace(scope_id: str, course_id: str) -> tuple[Any, int]:
    return jsonify(_service().workspace(scope_id, course_id)), 200


@web.get("/api/scopes/<scope_id>/workspace")
def all_workspaces(scope_id: str) -> tuple[Any, int]:
    return jsonify(_service().all_workspaces(scope_id)), 200


@web.post("/api/plans")
def create_plan() -> tuple[Any, int]:
    _require_browser_request()
    plan = _service().create_plan(_json_body())
    return jsonify({"plan": plan.to_dict()}), 201


@web.post("/api/batches")
def create_batch_plan() -> tuple[Any, int]:
    _require_browser_request()
    batch = _service().create_batch_plan(_json_body())
    return jsonify({"batch": batch.to_dict()}), 201


@web.post("/api/plans/<plan_id>/execute")
def execute_plan(plan_id: str) -> tuple[Any, int]:
    _require_browser_request()
    body = _json_body()
    if body.get("confirmation") != plan_id:
        raise ValidationError("Explicit confirmation of this preview is required.")
    return jsonify({"operation": _service().execute(plan_id)}), 200


@web.post("/api/batches/<batch_id>/execute")
def execute_batch_plan(batch_id: str) -> tuple[Any, int]:
    _require_browser_request()
    body = _json_body()
    if body.get("confirmation") != batch_id:
        raise ValidationError("Explicit confirmation of this batch preview is required.")
    return jsonify({"operation": _service().execute_batch(batch_id)}), 200


@web.get("/api/operations/<operation_id>")
def get_operation(operation_id: str) -> tuple[Any, int]:
    return jsonify({"operation": _service().get_operation(operation_id)}), 200


@web.post("/api/operations/<operation_id>/recheck")
def recheck_operation(operation_id: str) -> tuple[Any, int]:
    _require_browser_request()
    return jsonify({"operation": _service().recheck(operation_id)}), 200


@web.post("/api/operations/<operation_id>/rollback")
def rollback(operation_id: str) -> tuple[Any, int]:
    _require_browser_request()
    body = _json_body()
    if body.get("confirmation") != operation_id:
        raise ValidationError("Explicit rollback confirmation is required.")
    return jsonify({"operation": _service().rollback(operation_id)}), 200


@web.app_errorhandler(ValidationError)
def validation_error(error: ValidationError) -> tuple[Any, int]:
    return jsonify({"error": {"kind": "validation", "message": str(error)}}), 400


@web.app_errorhandler(ScopeError)
def scope_error(error: ScopeError) -> tuple[Any, int]:
    return jsonify({"error": {"kind": "scope", "message": str(error)}}), 400


@web.app_errorhandler(BackupError)
def backup_error(error: BackupError) -> tuple[Any, int]:
    return jsonify({"error": {"kind": "backup", "message": str(error)}}), 409


@web.app_errorhandler(CanvasError)
def canvas_error(error: CanvasError) -> tuple[Any, int]:
    return jsonify({"error": {"kind": error.kind, "message": str(error)}}), error.status


@web.app_errorhandler(HTTPException)
def http_error(error: HTTPException) -> Any:
    if request.path.startswith("/api/"):
        return (
            jsonify(
                {
                    "error": {
                        "kind": "http",
                        "message": error.description,
                    }
                }
            ),
            error.code or 500,
        )
    return error


@web.app_errorhandler(Exception)
def unexpected_error(error: Exception) -> tuple[Any, int]:
    current_app.logger.exception("Unhandled application error")
    return (
        jsonify(
            {
                "error": {
                    "kind": "internal",
                    "message": (
                        "The operation could not be completed. No further writes were attempted."
                    ),
                }
            }
        ),
        500,
    )
