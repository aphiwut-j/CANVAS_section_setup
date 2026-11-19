from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.models.scopes import CanvasScope, ScopedCourse
from app.services.backup import BackupError, BackupService
from app.services.canvas.client import CanvasError
from app.services.canvas.demo import DemoCanvasGateway
from app.services.operations import OperationService, OperationStore, PlanStore, ValidationError
from app.services.scopes import ScopeStore


class RecordingGateway(DemoCanvasGateway):
    def __init__(self) -> None:
        super().__init__()
        self.write_count = 0

    def create_override(self, course_id: str, assignment_id: str, values: dict):  # type: ignore[type-arg]
        self.write_count += 1
        return super().create_override(course_id, assignment_id, values)

    def update_override(
        self, course_id: str, assignment_id: str, override_id: str, values: dict  # type: ignore[type-arg]
    ):
        self.write_count += 1
        return super().update_override(course_id, assignment_id, override_id, values)

    def delete_override(self, course_id: str, assignment_id: str, override_id: str):
        self.write_count += 1
        return super().delete_override(course_id, assignment_id, override_id)

    def set_only_visible_to_overrides(
        self, course_id: str, assignment_id: str, enabled: bool
    ):
        self.write_count += 1
        return super().set_only_visible_to_overrides(course_id, assignment_id, enabled)


def service(tmp_path: Path, gateway: RecordingGateway) -> OperationService:
    scopes = ScopeStore()
    scopes.put(
        CanvasScope(
            id="test-scope",
            source_name="scope.csv",
            created_at="2026-08-14T00:00:00Z",
            courses=(
                ScopedCourse(
                    course=gateway.get_course("42"),
                    sections=(gateway.get_section("201"), gateway.get_section("202")),
                ),
            ),
        )
    )
    return OperationService(
        gateway=gateway,
        backup_service=BackupService(tmp_path / "backups"),
        plan_store=PlanStore(),
        scope_store=scopes,
        operation_store=OperationStore(tmp_path / "operations"),
    )


def multi_course_service(tmp_path: Path, gateway: RecordingGateway) -> OperationService:
    scopes = ScopeStore()
    scopes.put(
        CanvasScope(
            id="multi-scope",
            source_name="scope.csv",
            created_at="2026-08-14T00:00:00Z",
            courses=(
                ScopedCourse(
                    course=gateway.get_course("42"),
                    sections=(gateway.get_section("201"),),
                ),
                ScopedCourse(
                    course=gateway.get_course("43"),
                    sections=(gateway.get_section("211"),),
                ),
            ),
        )
    )
    return OperationService(
        gateway=gateway,
        backup_service=BackupService(tmp_path / "backups"),
        plan_store=PlanStore(),
        scope_store=scopes,
        operation_store=OperationStore(tmp_path / "operations"),
    )


def payload() -> dict:
    return {
        "scope_id": "test-scope",
        "course_id": "42",
        "restrict_assignments": True,
        "changes": [
            {
                "assignment_id": "301",
                "section_id": "201",
                "unlock_at": "2026-08-18T00:00:00Z",
                "due_at": "2026-08-25T00:00:00Z",
                "lock_at": "2026-08-26T00:00:00Z",
            },
            {
                "assignment_id": "302",
                "section_id": "202",
                "unlock_at": "2026-08-19T00:00:00Z",
                "due_at": "2026-08-27T00:00:00Z",
                "lock_at": "2026-08-28T00:00:00Z",
            },
        ],
    }


def batch_payload() -> dict:
    return {
        "scope_id": "multi-scope",
        "restrict_assignments": True,
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
    }


def test_plan_execute_verify_backup_and_rollback(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    original = deepcopy(gateway.list_assignments("42"))
    operations = service(tmp_path, gateway)
    plan = operations.create_plan(payload())

    assert [item.action for item in plan.items] == ["update", "create"]
    result = operations.execute(plan.id)

    assert result["status"] == "verified"
    assert (tmp_path / "backups" / result["backup_id"] / "manifest.json").is_file()
    changed = gateway.list_assignments("42")
    assert changed != original
    assert changed[0]["only_visible_to_overrides"] is True
    assert changed[1]["only_visible_to_overrides"] is True

    rollback = operations.rollback(result["id"])

    assert rollback["status"] == "rolled_back"
    assert gateway.list_assignments("42") == original


def test_batch_execute_backs_up_verifies_and_rolls_back_multiple_courses(
    tmp_path: Path,
) -> None:
    gateway = RecordingGateway()
    original_42 = deepcopy(gateway.list_assignments("42"))
    original_43 = deepcopy(gateway.list_assignments("43"))
    operations = multi_course_service(tmp_path, gateway)
    batch = operations.create_batch_plan(batch_payload())

    assert len(batch.plans) == 2
    result = operations.execute_batch(batch.id)

    assert result["status"] == "verified"
    assert set(result["backup_ids"]) == {"42", "43"}
    assert gateway.list_assignments("42") != original_42
    assert gateway.list_assignments("43") != original_43

    rollback = operations.rollback(result["id"])

    assert rollback["status"] == "rolled_back"
    assert gateway.list_assignments("42") == original_42
    assert gateway.list_assignments("43") == original_43


class FailingSecondBackupService(BackupService):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.calls = 0

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 2:
            raise BackupError("simulated second-course backup failure")
        return super().create(**kwargs)


def test_batch_backup_failure_stops_all_courses_before_write(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    base = multi_course_service(tmp_path, gateway)
    operations = OperationService(
        gateway=gateway,
        backup_service=FailingSecondBackupService(tmp_path / "batch-backups"),
        plan_store=PlanStore(),
        scope_store=base.scopes,
        operation_store=OperationStore(tmp_path / "batch-operations"),
    )
    batch = operations.create_batch_plan(batch_payload())

    with pytest.raises(BackupError, match="second-course"):
        operations.execute_batch(batch.id)

    assert gateway.write_count == 0


def test_stale_preview_stops_before_write_or_backup(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    operations = service(tmp_path, gateway)
    plan = operations.create_plan(payload())
    gateway._assignments["42"][0]["overrides"][0]["due_at"] = "2027-01-01T00:00:00Z"

    with pytest.raises(ValidationError, match="changed after the preview"):
        operations.execute(plan.id)

    assert gateway.write_count == 0
    assert not (tmp_path / "backups").exists()


class FailingBackupService(BackupService):
    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        raise BackupError("simulated backup failure")


def test_backup_failure_is_a_hard_stop(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    operations = OperationService(
        gateway=gateway,
        backup_service=FailingBackupService(tmp_path / "backups"),
        plan_store=PlanStore(),
        scope_store=service(tmp_path, gateway).scopes,
        operation_store=OperationStore(tmp_path / "operations"),
    )
    plan = operations.create_plan(payload())

    with pytest.raises(BackupError, match="simulated"):
        operations.execute(plan.id)

    assert gateway.write_count == 0


class UncertainCreateGateway(RecordingGateway):
    def create_override(self, course_id: str, assignment_id: str, values: dict):  # type: ignore[type-arg]
        super().create_override(course_id, assignment_id, values)
        raise CanvasError("simulated lost response", kind="uncertain_write")


def test_uncertain_create_can_be_reconciled_and_rolled_back(tmp_path: Path) -> None:
    gateway = UncertainCreateGateway()
    operations = service(tmp_path, gateway)
    create_only = payload()
    create_only["restrict_assignments"] = False
    create_only["changes"] = [create_only["changes"][1]]
    plan = operations.create_plan(create_only)

    recovery = operations.execute(plan.id)

    operation_files = list((tmp_path / "operations").glob("*.json"))
    operation_id = operation_files[0].stem
    assert recovery["status"] == "recovery_required"
    assert recovery["error"] == "simulated lost response"
    assert operations.operations.get(operation_id)["status"] == "recovery_required"
    result = operations.rollback(operation_id)

    assert result["status"] == "rolled_back"
    assert gateway.get_assignment("42", "302")["overrides"] == []


@pytest.mark.parametrize(
    ("unlock", "due", "lock", "message"),
    [
        ("2026-08-20T00:00:00Z", "2026-08-19T00:00:00Z", None, "Available"),
        (None, "2026-08-20T00:00:00Z", "2026-08-19T00:00:00Z", "Due"),
        (None, None, None, "at least one"),
    ],
)
def test_invalid_date_order_is_rejected(
    tmp_path: Path, unlock: str | None, due: str | None, lock: str | None, message: str
) -> None:
    operations = service(tmp_path, RecordingGateway())
    bad = payload()
    bad["changes"] = [
        {
            "assignment_id": "301",
            "section_id": "201",
            "unlock_at": unlock,
            "due_at": due,
            "lock_at": lock,
        }
    ]
    with pytest.raises(ValidationError, match=message):
        operations.create_plan(bad)


def test_plan_cannot_target_a_section_outside_uploaded_scope(tmp_path: Path) -> None:
    operations = service(tmp_path, RecordingGateway())
    outside = payload()
    outside["changes"] = [
        {
            "assignment_id": "301",
            "section_id": "203",
            "due_at": "2026-08-27T00:00:00Z",
        }
    ]

    with pytest.raises(ValidationError, match="no longer available"):
        operations.create_plan(outside)


def test_one_section_can_target_multiple_assignments_with_different_dates(
    tmp_path: Path,
) -> None:
    operations = service(tmp_path, RecordingGateway())
    bulk = payload()
    bulk["restrict_assignments"] = False
    bulk["changes"] = [
        {
            "assignment_id": "301",
            "section_id": "201",
            "due_at": "2026-08-25T00:00:00Z",
        },
        {
            "assignment_id": "302",
            "section_id": "201",
            "due_at": "2026-08-27T00:00:00Z",
        },
    ]

    plan = operations.create_plan(bulk)

    assert [item.assignment_id for item in plan.items] == ["301", "302"]
    assert [item.section_id for item in plan.items] == ["201", "201"]
    assert [item.after.due_at for item in plan.items] == [
        "2026-08-25T00:00:00Z",
        "2026-08-27T00:00:00Z",
    ]


def test_multiple_sections_and_assignments_can_share_dates(tmp_path: Path) -> None:
    operations = service(tmp_path, RecordingGateway())
    bulk = payload()
    shared_due_at = "2026-08-29T00:00:00Z"
    bulk["restrict_assignments"] = False
    bulk["changes"] = [
        {
            "assignment_id": assignment_id,
            "section_id": section_id,
            "due_at": shared_due_at,
        }
        for assignment_id in ("301", "302")
        for section_id in ("201", "202")
    ]

    plan = operations.create_plan(bulk)

    assert len(plan.items) == 4
    assert {(item.assignment_id, item.section_id) for item in plan.items} == {
        ("301", "201"),
        ("301", "202"),
        ("302", "201"),
        ("302", "202"),
    }
    assert {item.after.due_at for item in plan.items} == {shared_due_at}


def test_plan_rejects_more_than_500_section_assignment_combinations(
    tmp_path: Path,
) -> None:
    operations = service(tmp_path, RecordingGateway())
    oversized = payload()
    oversized["changes"] = [oversized["changes"][0]] * 501

    with pytest.raises(ValidationError, match="limited to 500"):
        operations.create_plan(oversized)
