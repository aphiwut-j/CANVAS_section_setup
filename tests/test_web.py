from __future__ import annotations

from io import BytesIO

from app import create_app
from app.services.canvas.client import UnconfiguredCanvasGateway
from app.services.canvas.demo import DemoCanvasGateway


def test_home_and_read_only_discovery() -> None:
    app = create_app(
        {"TESTING": True, "CANVAS_DEMO_MODE": True, "CANVAS_GATEWAY": DemoCanvasGateway()}
    )
    client = app.test_client()

    assert client.get("/").status_code == 200
    assert client.get("/api/courses").status_code == 404
    scope_response = client.post(
        "/api/scopes",
        data={"file": (BytesIO(b"course_id,section_id\n42,201\n"), "scope.csv")},
        headers={"X-Requested-With": "CanvasSectionPlanner"},
        content_type="multipart/form-data",
    )
    assert scope_response.status_code == 201
    scope_id = scope_response.get_json()["scope"]["id"]
    workspace = client.get(f"/api/scopes/{scope_id}/courses/42/workspace")
    assert workspace.status_code == 200
    assert [section["id"] for section in workspace.get_json()["sections"]] == ["201"]


def test_url_scope_loads_all_provided_courses_and_builds_batch_preview() -> None:
    app = create_app(
        {"TESTING": True, "CANVAS_DEMO_MODE": True, "CANVAS_GATEWAY": DemoCanvasGateway()}
    )
    client = app.test_client()
    scope_response = client.post(
        "/api/scopes",
        data={
            "file": (
                BytesIO(
                    b"section_url\n"
                    b"https://demo.canvas.invalid/courses/42/sections/201\n"
                    b"https://demo.canvas.invalid/courses/43/sections/211\n"
                ),
                "scope.csv",
            )
        },
        headers={"X-Requested-With": "CanvasSectionPlanner"},
        content_type="multipart/form-data",
    )
    assert scope_response.status_code == 201
    scope_id = scope_response.get_json()["scope"]["id"]

    workspaces = client.get(f"/api/scopes/{scope_id}/workspace")
    assert workspaces.status_code == 200
    assert [row["course"]["id"] for row in workspaces.get_json()["courses"]] == [
        "42",
        "43",
    ]

    preview = client.post(
        "/api/batches",
        json={
            "scope_id": scope_id,
            "restrict_assignments": False,
            "course_changes": [
                {
                    "course_id": "42",
                    "changes": [
                        {
                            "assignment_id": "302",
                            "section_id": "201",
                            "due_at": "2026-09-01T00:00:00Z",
                        }
                    ],
                },
                {
                    "course_id": "43",
                    "changes": [
                        {
                            "assignment_id": "311",
                            "section_id": "211",
                            "due_at": "2026-09-01T00:00:00Z",
                        }
                    ],
                },
            ],
        },
        headers={"X-Requested-With": "CanvasSectionPlanner"},
    )
    assert preview.status_code == 201
    batch = preview.get_json()["batch"]
    assert len(batch["courses"]) == 2
    assert {(item["course_id"], item["section_id"]) for item in batch["items"]} == {
        ("42", "201"),
        ("43", "211"),
    }


def test_favicon_and_missing_page_keep_their_http_status() -> None:
    app = create_app(
        {"TESTING": True, "CANVAS_DEMO_MODE": True, "CANVAS_GATEWAY": DemoCanvasGateway()}
    )
    client = app.test_client()

    assert client.get("/favicon.ico").status_code == 204
    assert client.get("/missing-page").status_code == 404
    api_missing = client.get("/api/missing-page")
    assert api_missing.status_code == 404
    assert api_missing.get_json()["error"]["kind"] == "http"


def test_unconfigured_canvas_has_actionable_status_and_safe_503() -> None:
    app = create_app(
        {
            "TESTING": True,
            "CANVAS_DEMO_MODE": False,
            "CANVAS_GATEWAY": UnconfiguredCanvasGateway("Canvas configuration is missing."),
        }
    )
    client = app.test_client()

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.get_json()["configured"] is False
    scope_response = client.post(
        "/api/scopes",
        data={"file": (BytesIO(b"course_id,section_id\n42,201\n"), "scope.csv")},
        headers={"X-Requested-With": "CanvasSectionPlanner"},
        content_type="multipart/form-data",
    )
    assert scope_response.status_code == 503
    assert scope_response.get_json() == {
        "error": {"kind": "configuration", "message": "Canvas configuration is missing."}
    }


def test_mutation_routes_require_web_header_and_confirmation() -> None:
    app = create_app(
        {"TESTING": True, "CANVAS_DEMO_MODE": True, "CANVAS_GATEWAY": DemoCanvasGateway()}
    )
    client = app.test_client()
    imported = client.post(
        "/api/scopes",
        data={"file": (BytesIO(b"course_id,section_id\n42,202\n"), "scope.csv")},
        headers={"X-Requested-With": "CanvasSectionPlanner"},
        content_type="multipart/form-data",
    ).get_json()["scope"]
    change = {
        "scope_id": imported["id"],
        "course_id": "42",
        "changes": [
            {
                "assignment_id": "302",
                "section_id": "202",
                "due_at": "2026-08-27T00:00:00Z",
            }
        ],
    }

    assert client.post("/api/plans", json=change).status_code == 400
    preview = client.post(
        "/api/plans", json=change, headers={"X-Requested-With": "CanvasSectionPlanner"}
    )
    assert preview.status_code == 201
    plan_id = preview.get_json()["plan"]["id"]
    rejected = client.post(
        f"/api/plans/{plan_id}/execute",
        json={"confirmation": "wrong"},
        headers={"X-Requested-With": "CanvasSectionPlanner"},
    )
    assert rejected.status_code == 400
