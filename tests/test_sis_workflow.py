from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest

from app import create_app
from app.services.backup import BackupError, BackupService
from app.services.canvas.demo import DemoCanvasGateway
from app.services.operations import OperationStore

HEADERS = {"X-Requested-With": "CanvasSectionPlanner"}
REPORT = (
    b"course_id,section_id,status,start_date,end_date\n"
    b"DEMO-42,DEMO-201-Re-Assessment,active,2026-08-01T00:00:00+11:00,2026-12-27T00:00:00+11:00\n"
    b"DEMO-43,DEMO-211-Re-Assessment,active,2026-12-21T00:00:00+11:00,2030-12-30T00:00:00+11:00\n"
    b"DEMO-42,DEMO-202-Regular,active,2026-08-01T00:00:00+11:00,2026-12-27T00:00:00+11:00\n"
)


def setup_workflow(tmp_path: Path):
    gateway = DemoCanvasGateway()
    app = create_app({"TESTING": True, "CANVAS_DEMO_MODE": True, "CANVAS_GATEWAY": gateway})
    service = app.extensions["operation_service"]
    service.backups = BackupService(tmp_path / "backups")
    service.operations = OperationStore(tmp_path / "operations")
    client = app.test_client()
    imported = client.post(
        "/api/scopes",
        headers=HEADERS,
        data={"import_mode": "sis_dates", "file": (BytesIO(REPORT), "report.csv")},
    )
    assert imported.status_code == 201
    scope = imported.get_json()["scope"]
    assert scope["skipped_rows"] == 1
    body = {
        "scope_id": scope["id"],
        "restrict_assignments": False,
        "due_policy": "preserve",
        "course_changes": [
            {
                "course_id": "42",
                "changes": [
                    {
                        "assignment_id": "301",
                        "section_id": "201",
                        "unlock_at": "2000-01-01T00:00:00Z",
                        "lock_at": "2001-01-01T00:00:00Z",
                    },
                ],
            },
            {"course_id": "43", "changes": [{"assignment_id": "311", "section_id": "211"}]},
        ],
    }
    return client, service, gateway, body


@pytest.mark.parametrize("restrict", [True, False])
@pytest.mark.parametrize("due_policy", ["day_before_end", "preserve", "end", "none"])
def test_sis_preview_apply_backup_verify_and_rollback(
    tmp_path: Path, restrict: bool, due_policy: str
) -> None:
    client, service, gateway, body = setup_workflow(tmp_path)
    originals = {course: deepcopy(gateway.list_assignments(course)) for course in ("42", "43")}
    body.update(restrict_assignments=restrict, due_policy=due_policy)
    preview = client.post("/api/batches", json=body, headers=HEADERS)
    assert preview.status_code == 201
    batch = preview.get_json()["batch"]
    first, second = batch["items"]
    assert first["after"]["unlock_at"] == "2026-07-31T13:00:00Z"
    assert first["after"]["lock_at"] == "2026-12-27T12:59:59Z"
    assert second["after"]["lock_at"] == "2030-12-30T12:59:59Z"
    assert (
        first["after"]["due_at"]
        == {
            "day_before_end": "2026-12-26T12:59:59Z",
            "preserve": "2026-08-24T13:59:00Z",
            "end": "2026-12-27T12:59:59Z",
            "none": None,
        }[due_policy]
    )
    assert not (tmp_path / "backups").exists()
    for course in originals:
        assert gateway.list_assignments(course) == originals[course]
    url = f"/api/batches/{batch['id']}/execute"
    assert client.post(url, json={"confirmation": "wrong"}, headers=HEADERS).status_code == 400
    result = client.post(url, json={"confirmation": batch["id"]}, headers=HEADERS)
    assert result.status_code == 200
    operation = result.get_json()["operation"]
    assert operation["status"] == "verified"
    for backup_id in operation["backup_ids"].values():
        assert (tmp_path / "backups" / backup_id / "manifest.json").is_file()
        assert service.backups.read_before(backup_id)
    assert gateway.get_assignment("42", "301")["only_visible_to_overrides"] is restrict
    rolled_back = client.post(
        f"/api/operations/{operation['id']}/rollback",
        json={"confirmation": operation["id"]},
        headers=HEADERS,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.get_json()["operation"]["status"] == "rolled_back"
    for course in originals:
        assert gateway.list_assignments(course) == originals[course]


def test_default_csv_due_date_is_one_day_before_end(tmp_path: Path) -> None:
    client, _service, _gateway, body = setup_workflow(tmp_path)
    body.pop("due_policy")
    response = client.post("/api/batches", json=body, headers=HEADERS)
    assert response.status_code == 201
    first, second = response.get_json()["batch"]["items"]
    assert first["after"]["due_at"] == "2026-12-26T12:59:59Z"
    assert second["after"]["due_at"] == "2030-12-29T12:59:59Z"
    assert first["after"]["lock_at"] == "2026-12-27T12:59:59Z"


def test_one_day_offset_cannot_precede_section_start(tmp_path: Path) -> None:
    client, service, _gateway, body = setup_workflow(tmp_path)
    body["due_policy"] = "day_before_end"
    section = service.scopes.get(body["scope_id"]).courses[0].sections[0]
    section["start_date"] = "2026-12-26T13:00:00Z"
    section["end_date"] = "2026-12-27T01:00:00Z"
    response = client.post("/api/batches", json=body, headers=HEADERS)
    assert response.status_code == 400
    assert (
        "Available date must not be after the due date" in response.get_json()["error"]["message"]
    )
    assert not (tmp_path / "backups").exists()


def test_csv_due_date_conflict_is_actionable_and_read_only(tmp_path: Path) -> None:
    client, _service, gateway, body = setup_workflow(tmp_path)
    gateway._assignments["42"][0]["overrides"][0]["due_at"] = "2026-01-01T00:00:00Z"
    response = client.post("/api/batches", json=body, headers=HEADERS)
    assert response.status_code == 400
    assert "Choose another CSV due-date policy" in response.get_json()["error"]["message"]
    assert not (tmp_path / "backups").exists()


def test_csv_rejects_section_excluded_by_filter(tmp_path: Path) -> None:
    client, _service, _gateway, body = setup_workflow(tmp_path)
    body["course_changes"][0]["changes"][0]["section_id"] = "202"
    assert client.post("/api/batches", json=body, headers=HEADERS).status_code == 400


def test_csv_backup_failure_stops_before_any_change(tmp_path: Path, monkeypatch) -> None:
    client, service, gateway, body = setup_workflow(tmp_path)
    originals = {course: gateway.list_assignments(course) for course in ("42", "43")}
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]

    def fail(**kwargs):
        raise BackupError("Backup verification failed")

    monkeypatch.setattr(service.backups, "create", fail)
    response = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    )
    assert response.status_code == 409
    for course in originals:
        assert gateway.list_assignments(course) == originals[course]


def test_changed_base_due_date_invalidates_preview(tmp_path: Path) -> None:
    client, _service, gateway, body = setup_workflow(tmp_path)
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    gateway._assignments["43"][0]["due_at"] = "2027-01-01T00:00:00Z"
    response = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    )
    assert response.status_code == 400
    assert not (tmp_path / "backups").exists()


def test_inherited_due_date_is_preserved_and_rollback_restores_inheritance(tmp_path: Path) -> None:
    client, _service, gateway, body = setup_workflow(tmp_path)
    override = gateway._assignments["42"][0]["overrides"][0]
    override.pop("due_at")
    override.pop("lock_at")
    original = gateway.get_assignment("42", "301")
    preview = client.post("/api/batches", json=body, headers=HEADERS)
    assert preview.status_code == 201
    batch = preview.get_json()["batch"]
    assert batch["items"][0]["after"]["due_at"] == original["due_at"]
    result = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    ).get_json()["operation"]
    assert result["status"] == "verified"
    rolled_back = client.post(
        f"/api/operations/{result['id']}/rollback",
        json={"confirmation": result["id"]},
        headers=HEADERS,
    )
    assert rolled_back.get_json()["operation"]["status"] == "rolled_back"
    assert gateway.get_assignment("42", "301") == original


def test_second_preview_recognizes_normalized_dates_as_no_change(tmp_path: Path) -> None:
    client, _service, _gateway, body = setup_workflow(tmp_path)
    body["due_policy"] = "day_before_end"
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    result = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    )
    assert result.get_json()["operation"]["status"] == "verified"
    second = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    assert {item["action"] for item in second["items"]} == {"no_change"}


def test_timezone_change_rejects_execution_before_backup(tmp_path: Path) -> None:
    client, _service, gateway, body = setup_workflow(tmp_path)
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    gateway.time_zone = "UTC"
    response = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    )
    assert response.status_code == 400
    assert "timezone changed" in response.get_json()["error"]["message"]
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_saved_recovery_operation_rechecks_without_canvas_writes(
    tmp_path: Path, monkeypatch, legacy: bool
) -> None:
    client, service, gateway, body = setup_workflow(tmp_path)
    body["due_policy"] = "day_before_end"
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    public = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    ).get_json()["operation"]
    operation = service.operations.get(public["id"])
    operation["status"] = "recovery_required"
    operation["error"] = "Previous midnight verification mismatch"
    original_applied = deepcopy(operation["applied"])
    service.operations.save(operation)
    if legacy:
        for course_id, backup_id in operation["backup_ids"].items():
            path = service.backups.root / backup_id / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["planned_change"].pop("canvas_time_zone")
            due, lock = (
                ("2026-12-25T13:00:00Z", "2026-12-26T13:00:00Z")
                if course_id == "42"
                else ("2030-12-28T13:00:00Z", "2030-12-29T13:00:00Z")
            )
            for item in manifest["planned_change"]["items"]:
                item["after"].update(due_at=due, lock_at=lock)
            path.write_text(json.dumps(manifest), encoding="utf-8")

    def blocked(*args, **kwargs):
        raise AssertionError("Recheck must not write to Canvas")

    for method in (
        "create_override",
        "update_override",
        "delete_override",
        "set_only_visible_to_overrides",
    ):
        monkeypatch.setattr(gateway, method, blocked)
    url = f"/api/operations/{operation['id']}"
    assert client.get(url).get_json()["operation"]["status"] == "recovery_required"
    assert client.post(url + "/recheck").status_code == 400
    checked = client.post(url + "/recheck", headers=HEADERS)
    assert checked.status_code == 200
    assert checked.get_json()["operation"]["status"] == "verified"
    saved = service.operations.get(operation["id"])
    assert saved["applied"] == original_applied
    assert saved["verification_checks"][-1]["remote_writes"] == 0
    assert saved["verification_checks"][-1]["previous_error"] == operation["error"]
    # A real mismatch must remain a recovery state; normalization is not a tolerance.
    gateway._assignments["42"][0]["overrides"][0]["lock_at"] = "2026-12-27T12:58:59Z"
    mismatch = client.post(url + "/recheck", headers=HEADERS)
    assert mismatch.get_json()["operation"]["status"] == "recovery_required"
    reported = mismatch.get_json()["operation"]
    assert reported["verification_complete"] is True
    assert len(reported["verification_differences"]) == 1
    assert reported["verification_differences"][0]["actual"]["lock_at"] == "2026-12-27T12:58:59Z"


def test_recheck_refuses_corrupt_backup(tmp_path: Path) -> None:
    client, service, _gateway, body = setup_workflow(tmp_path)
    batch = client.post("/api/batches", json=body, headers=HEADERS).get_json()["batch"]
    public = client.post(
        f"/api/batches/{batch['id']}/execute", json={"confirmation": batch["id"]}, headers=HEADERS
    ).get_json()["operation"]
    backup_id = next(iter(public["backup_ids"].values()))
    (service.backups.root / backup_id / "before.json").write_text("[]", encoding="utf-8")
    response = client.post(f"/api/operations/{public['id']}/recheck", headers=HEADERS)
    assert response.status_code == 409
