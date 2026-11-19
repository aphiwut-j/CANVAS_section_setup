from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.models.batches import BatchChangePlan
from app.models.plans import ChangePlan, DateSet, PlanItem
from app.services.backup import BackupService
from app.services.canvas.client import CanvasError
from app.services.canvas.dates import normalize_override_dates
from app.services.canvas.gateway import CanvasGateway
from app.services.scopes import ScopeStore


class ValidationError(ValueError):
    pass


MAX_PLAN_ITEMS = 500


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(value: Any) -> str:
    return str(value)


def _dates(value: dict[str, Any]) -> DateSet:
    return DateSet(
        unlock_at=value.get("unlock_at"),
        due_at=value.get("due_at"),
        lock_at=value.get("lock_at"),
    )


def _parse_time(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO 8601 date and time.") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{label} must include a timezone.")
    return parsed


def _validate_dates(dates: DateSet) -> None:
    unlock = _parse_time(dates.unlock_at, "Available date")
    due = _parse_time(dates.due_at, "Due date")
    lock = _parse_time(dates.lock_at, "Close date")
    if not any((unlock, due, lock)):
        raise ValidationError("Each section assignment needs at least one date.")
    if unlock and due and unlock > due:
        raise ValidationError("Available date must not be after the due date.")
    if due and lock and due > lock:
        raise ValidationError("Due date must not be after the close date.")
    if unlock and lock and unlock > lock:
        raise ValidationError("Available date must not be after the close date.")


def _same_dates(left: DateSet | None, right: DateSet) -> bool:
    if left is None:
        return False
    return all(
        _parse_time(getattr(left, field), field) == _parse_time(getattr(right, field), field)
        for field in ("unlock_at", "due_at", "lock_at")
    )


def _restore_override_values(plan: ChangePlan, item: PlanItem) -> dict[str, Any]:
    original = next(
        row
        for row in plan.assignment_snapshots[item.assignment_id].get("overrides", [])
        if _id(row.get("id")) == item.override_id
    )
    # Canvas PUT removes date overrides for omitted fields. Preserve absence vs null.
    return {
        field: original[field] for field in ("unlock_at", "due_at", "lock_at") if field in original
    }


def _state_fingerprint(assignment: dict[str, Any]) -> str:
    state = {
        "id": _id(assignment.get("id")),
        "due_at": assignment.get("due_at"),
        "only_visible_to_overrides": bool(assignment.get("only_visible_to_overrides")),
        "overrides": sorted(
            [
                {
                    "id": _id(row.get("id")),
                    "course_section_id": (
                        _id(row["course_section_id"])
                        if row.get("course_section_id") is not None
                        else None
                    ),
                    "student_ids": sorted(_id(item) for item in row.get("student_ids", [])),
                    "group_id": _id(row["group_id"]) if row.get("group_id") is not None else None,
                    "unlock_at": row.get("unlock_at"),
                    "due_at": row.get("due_at"),
                    "lock_at": row.get("lock_at"),
                    "date_fields": sorted(
                        key for key in ("unlock_at", "due_at", "lock_at") if key in row
                    ),
                    "all_day": row.get("all_day"),
                    "all_day_date": row.get("all_day_date"),
                }
                for row in assignment.get("overrides", [])
            ],
            key=lambda row: row["id"],
        ),
    }
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class PlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, ChangePlan] = {}
        self._lock = threading.Lock()

    def put(self, plan: ChangePlan) -> None:
        with self._lock:
            self._plans[plan.id] = plan

    def get(self, plan_id: str) -> ChangePlan:
        with self._lock:
            try:
                return self._plans[plan_id]
            except KeyError as exc:
                raise ValidationError("This preview has expired. Create a new preview.") from exc


class BatchPlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, BatchChangePlan] = {}
        self._lock = threading.Lock()

    def put(self, plan: BatchChangePlan) -> None:
        with self._lock:
            self._plans[plan.id] = plan

    def get(self, plan_id: str) -> BatchChangePlan:
        with self._lock:
            try:
                return self._plans[plan_id]
            except KeyError as exc:
                raise ValidationError(
                    "This batch preview has expired. Create a new preview."
                ) from exc


class OperationStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(".agent/operations")
        self._lock = threading.Lock()

    def save(self, operation: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{operation['id']}.json"
        temporary = target.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(json.dumps(operation, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(target)

    def get(self, operation_id: str) -> dict[str, Any]:
        target = self.root / f"{operation_id}.json"
        if not target.is_file():
            raise ValidationError("Operation record was not found.")
        result = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValidationError("Operation record is invalid.")
        return result


class OperationService:
    def __init__(
        self,
        *,
        gateway: CanvasGateway,
        backup_service: BackupService,
        plan_store: PlanStore,
        scope_store: ScopeStore,
        batch_plan_store: BatchPlanStore | None = None,
        operation_store: OperationStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.backups = backup_service
        self.plans = plan_store
        self.batch_plans = batch_plan_store or BatchPlanStore()
        self.scopes = scope_store
        self.operations = operation_store or OperationStore()
        self._execute_lock = threading.Lock()

    def workspace(self, scope_id: str, course_id: str) -> dict[str, Any]:
        scoped_course = self.scopes.course(scope_id, course_id)
        return {
            "course": scoped_course.course,
            "sections": list(scoped_course.sections),
            "assignments": self.gateway.list_assignments(course_id),
        }

    def all_workspaces(self, scope_id: str) -> dict[str, Any]:
        scope = self.scopes.get(scope_id)
        courses = [
            {
                "course": scoped.course,
                "sections": list(scoped.sections),
                "assignments": self.gateway.list_assignments(_id(scoped.course.get("id"))),
            }
            for scoped in scope.courses
        ]
        return {"scope_id": scope.id, "courses": courses}

    def create_batch_plan(self, payload: dict[str, Any]) -> BatchChangePlan:
        scope_id = _id(payload.get("scope_id", "")).strip()
        course_changes = payload.get("course_changes")
        if not scope_id or not isinstance(course_changes, list) or not course_changes:
            raise ValidationError(
                "Upload a scope and select sections and assignments in at least one course."
            )
        total_items = sum(
            len(row.get("changes", []))
            for row in course_changes
            if isinstance(row, dict) and isinstance(row.get("changes"), list)
        )
        if total_items > MAX_PLAN_ITEMS:
            raise ValidationError(
                f"A preview is limited to {MAX_PLAN_ITEMS} section-assignment combinations."
            )
        seen_courses: set[str] = set()
        plans: list[ChangePlan] = []
        canvas_time_zone = self.gateway.get_time_zone()
        for row in course_changes:
            if not isinstance(row, dict):
                raise ValidationError("A course change group is invalid.")
            course_id = _id(row.get("course_id", "")).strip()
            if not course_id or course_id in seen_courses:
                raise ValidationError("Each selected course may appear only once.")
            seen_courses.add(course_id)
            plans.append(
                self.create_plan(
                    {
                        "scope_id": scope_id,
                        "course_id": course_id,
                        "changes": row.get("changes"),
                        "due_policy": payload.get("due_policy", "day_before_end"),
                        "restrict_assignments": bool(payload.get("restrict_assignments", False)),
                    },
                    canvas_time_zone=canvas_time_zone,
                )
            )
        batch = BatchChangePlan(
            id=uuid.uuid4().hex,
            scope_id=scope_id,
            plans=tuple(plans),
            created_at=_now(),
        )
        self.batch_plans.put(batch)
        return batch

    def create_plan(
        self, payload: dict[str, Any], *, canvas_time_zone: str | None = None
    ) -> ChangePlan:
        course_id = _id(payload.get("course_id", "")).strip()
        scope_id = _id(payload.get("scope_id", "")).strip()
        raw_changes = payload.get("changes")
        if not scope_id or not course_id or not isinstance(raw_changes, list) or not raw_changes:
            raise ValidationError(
                "Upload a scope, choose a course, and add at least one section assignment."
            )
        if len(raw_changes) > MAX_PLAN_ITEMS:
            raise ValidationError(
                f"A preview is limited to {MAX_PLAN_ITEMS} section-assignment combinations."
            )
        workspace = self.workspace(scope_id, course_id)
        canvas_time_zone = canvas_time_zone or self.gateway.get_time_zone()
        converted_midnight = False
        csv_dates = self.scopes.get(scope_id).import_mode == "sis_dates"
        due_policy = payload.get("due_policy", "day_before_end")
        if csv_dates and due_policy not in {"day_before_end", "preserve", "end", "none"}:
            raise ValidationError("Select a supported due-date policy.")
        sections = {_id(row.get("id")): row for row in workspace["sections"]}
        assignments = {_id(row.get("id")): row for row in workspace["assignments"]}
        seen: set[tuple[str, str]] = set()
        items: list[PlanItem] = []
        for change in raw_changes:
            if not isinstance(change, dict):
                raise ValidationError("A change row is invalid.")
            assignment_id = _id(change.get("assignment_id", ""))
            section_id = _id(change.get("section_id", ""))
            if assignment_id not in assignments or section_id not in sections:
                raise ValidationError("A selected assignment or section is no longer available.")
            if (assignment_id, section_id) in seen:
                raise ValidationError("An assignment and section pair may appear only once.")
            seen.add((assignment_id, section_id))
            after = DateSet(
                unlock_at=change.get("unlock_at") or None,
                due_at=change.get("due_at") or None,
                lock_at=change.get("lock_at") or None,
            )
            existing = next(
                (
                    row
                    for row in assignments[assignment_id].get("overrides", [])
                    if _id(row.get("course_section_id")) == section_id
                ),
                None,
            )
            if csv_dates:
                section = sections[section_id]
                due = (
                    existing.get("due_at")
                    if existing and "due_at" in existing
                    else assignments[assignment_id].get("due_at")
                )
                if due_policy == "day_before_end":
                    due = (
                        (
                            datetime.fromisoformat(section["end_date"].replace("Z", "+00:00"))
                            - timedelta(days=1)
                        )
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                after = DateSet(
                    unlock_at=section["start_date"],
                    lock_at=section["end_date"],
                    due_at=(
                        section["end_date"]
                        if due_policy == "end"
                        else None
                        if due_policy == "none"
                        else due
                    ),
                )
            try:
                normalized = normalize_override_dates(after, canvas_time_zone)
                converted_midnight = converted_midnight or normalized != after
                after = normalized
                _validate_dates(after)
            except ValueError as exc:
                raise ValidationError(
                    f"{assignments[assignment_id].get('name', assignment_id)} / "
                    f"{sections[section_id].get('name', section_id)}: {exc}"
                    + (" Choose another CSV due-date policy if needed." if csv_dates else "")
                ) from exc
            before = _dates(existing) if existing else None
            action: Literal["create", "update", "no_change"]
            if existing is None:
                action = "create"
            elif _same_dates(before, after) and all(
                field in existing for field in ("unlock_at", "due_at", "lock_at")
            ):
                action = "no_change"
            else:
                action = "update"
            items.append(
                PlanItem(
                    assignment_id=assignment_id,
                    assignment_name=str(assignments[assignment_id].get("name", assignment_id)),
                    section_id=section_id,
                    section_name=str(sections[section_id].get("name", section_id)),
                    action=action,
                    override_id=_id(existing["id"]) if existing else None,
                    before=before,
                    after=after,
                )
            )
        restrict = bool(payload.get("restrict_assignments", False))
        selected_assignment_ids = {item.assignment_id for item in items}
        warnings: list[str] = []
        if csv_dates:
            policy_label = {
                "day_before_end": "one day before the section end date",
                "preserve": "keep the existing due date",
                "end": "use the section end date",
                "none": "no due date",
            }[due_policy]
            warnings.append(
                "CSV start_date sets Available from; end_date sets Closes. "
                f"Due date: {policy_label}."
            )
        warnings.append(f"Canvas date interpretation uses API user timezone: {canvas_time_zone}.")
        if converted_midnight:
            warnings.append(
                "Canvas converts midnight Due and Available until to 11:59:59 pm on the same "
                "local date. The preview shows these converted times; Available from is unchanged."
            )
        warnings.append(
            "Everyone audience will be removed from selected assignments."
            if restrict
            else "Everyone audience visibility will be left unchanged."
        )
        if restrict:
            for assignment_id in sorted(selected_assignment_ids):
                other_count = sum(
                    1
                    for row in assignments[assignment_id].get("overrides", [])
                    if (_id(row.get("course_section_id")), assignment_id)
                    not in {(item.section_id, item.assignment_id) for item in items}
                )
                if other_count:
                    warnings.append(
                        f"{assignments[assignment_id].get('name')}: "
                        f"{other_count} existing override(s) "
                        "for other recipients will be preserved."
                    )
        snapshots = {
            assignment_id: assignments[assignment_id] for assignment_id in selected_assignment_ids
        }
        plan = ChangePlan(
            id=uuid.uuid4().hex,
            scope_id=scope_id,
            course_id=course_id,
            course_name=str(workspace["course"].get("name", course_id)),
            restrict_assignments=restrict,
            items=items,
            assignment_snapshots=snapshots,
            created_at=_now(),
            warnings=warnings,
            canvas_time_zone=canvas_time_zone,
        )
        self.plans.put(plan)
        return plan

    def execute(self, plan_id: str) -> dict[str, Any]:
        plan = self.plans.get(plan_id)
        with self._execute_lock:
            self._check_time_zone([plan])
            assignment_ids = sorted(plan.assignment_snapshots)
            fresh = [
                self.gateway.get_assignment(plan.course_id, item_id) for item_id in assignment_ids
            ]
            fresh_by_id = {_id(row["id"]): row for row in fresh}
            for assignment_id in assignment_ids:
                if _state_fingerprint(fresh_by_id[assignment_id]) != _state_fingerprint(
                    plan.assignment_snapshots[assignment_id]
                ):
                    raise ValidationError(
                        "Canvas changed after the preview. "
                        "Nothing was written; create a new preview."
                    )
            operation_id = uuid.uuid4().hex
            backup = self.backups.create(
                operation_id=operation_id,
                host_identifier=self.gateway.host_identifier,
                course_id=plan.course_id,
                before=fresh,
                planned_change=plan.to_dict(),
            )
            operation: dict[str, Any] = {
                "id": operation_id,
                "plan_id": plan.id,
                "course_id": plan.course_id,
                "course_name": plan.course_name,
                "status": "running",
                "created_at": _now(),
                "backup_id": backup["backup_id"],
                "applied": [],
                "error": None,
            }
            self.operations.save(operation)
            try:
                change_record: dict[str, Any]
                for item in plan.items:
                    if item.action == "no_change":
                        continue
                    values = asdict(item.after)
                    if item.action == "create":
                        values["course_section_id"] = item.section_id
                        change_record = {
                            "kind": "created_override",
                            "state": "pending",
                            "assignment_id": item.assignment_id,
                            "section_id": item.section_id,
                            "override_id": None,
                            "before": None,
                            "after": asdict(item.after),
                        }
                        operation["applied"].append(change_record)
                        self.operations.save(operation)
                        result = self.gateway.create_override(
                            plan.course_id, item.assignment_id, values
                        )
                        if result.get("id") is None:
                            raise CanvasError(
                                "Canvas created an override but did not return its identifier.",
                                kind="uncertain_write",
                            )
                        change_record["override_id"] = _id(result.get("id"))
                        change_record["state"] = "applied"
                    else:
                        assert item.override_id is not None and item.before is not None
                        change_record = {
                            "kind": "updated_override",
                            "state": "pending",
                            "assignment_id": item.assignment_id,
                            "section_id": item.section_id,
                            "override_id": item.override_id,
                            "before": _restore_override_values(plan, item),
                            "after": asdict(item.after),
                        }
                        operation["applied"].append(change_record)
                        self.operations.save(operation)
                        self.gateway.update_override(
                            plan.course_id, item.assignment_id, item.override_id, values
                        )
                        change_record["state"] = "applied"
                    self.operations.save(operation)
                if plan.restrict_assignments:
                    for assignment_id in assignment_ids:
                        before_visibility = bool(
                            plan.assignment_snapshots[assignment_id].get(
                                "only_visible_to_overrides", False
                            )
                        )
                        if not before_visibility:
                            change_record = {
                                "kind": "visibility",
                                "state": "pending",
                                "assignment_id": assignment_id,
                                "before": before_visibility,
                                "after": True,
                            }
                            operation["applied"].append(change_record)
                            self.operations.save(operation)
                            self.gateway.set_only_visible_to_overrides(
                                plan.course_id, assignment_id, True
                            )
                            change_record["state"] = "applied"
                            self.operations.save(operation)
                self._verify_plan(plan)
                operation["status"] = "verified"
                operation["verified_at"] = _now()
            except Exception as exc:
                uncertain = isinstance(exc, CanvasError) and exc.kind == "uncertain_write"
                applied = any(change.get("state") == "applied" for change in operation["applied"])
                operation["status"] = "recovery_required" if uncertain or applied else "failed"
                operation["uncertain_write"] = uncertain
                operation["error"] = str(exc)
                self.operations.save(operation)
                if operation["status"] == "recovery_required":
                    return self._public_operation(operation)
                raise
            self.operations.save(operation)
            return self._public_operation(operation)

    def execute_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.batch_plans.get(batch_id)
        with self._execute_lock:
            self._check_time_zone(list(batch.plans))
            fresh_by_course: dict[str, list[dict[str, Any]]] = {}
            for plan in batch.plans:
                assignment_ids = sorted(plan.assignment_snapshots)
                fresh = [
                    self.gateway.get_assignment(plan.course_id, assignment_id)
                    for assignment_id in assignment_ids
                ]
                fresh_by_id = {_id(row["id"]): row for row in fresh}
                for assignment_id in assignment_ids:
                    if _state_fingerprint(fresh_by_id[assignment_id]) != _state_fingerprint(
                        plan.assignment_snapshots[assignment_id]
                    ):
                        raise ValidationError(
                            "Canvas changed after the preview. "
                            "Nothing was written; create a new preview."
                        )
                fresh_by_course[plan.course_id] = fresh

            operation_id = uuid.uuid4().hex
            backup_ids: dict[str, str] = {}
            for plan in batch.plans:
                backup = self.backups.create(
                    operation_id=f"{operation_id}_{plan.course_id}",
                    host_identifier=self.gateway.host_identifier,
                    course_id=plan.course_id,
                    before=fresh_by_course[plan.course_id],
                    planned_change=plan.to_dict(),
                )
                backup_ids[plan.course_id] = str(backup["backup_id"])

            operation: dict[str, Any] = {
                "id": operation_id,
                "batch_id": batch.id,
                "course_ids": [plan.course_id for plan in batch.plans],
                "course_names": [plan.course_name for plan in batch.plans],
                "status": "running",
                "created_at": _now(),
                "backup_ids": backup_ids,
                "applied": [],
                "error": None,
            }
            self.operations.save(operation)
            try:
                for plan in batch.plans:
                    assignment_ids = sorted(plan.assignment_snapshots)
                    for item in plan.items:
                        if item.action == "no_change":
                            continue
                        values = asdict(item.after)
                        change_record: dict[str, Any]
                        if item.action == "create":
                            values["course_section_id"] = item.section_id
                            change_record = {
                                "kind": "created_override",
                                "state": "pending",
                                "course_id": plan.course_id,
                                "assignment_id": item.assignment_id,
                                "section_id": item.section_id,
                                "override_id": None,
                                "before": None,
                                "after": asdict(item.after),
                            }
                            operation["applied"].append(change_record)
                            self.operations.save(operation)
                            result = self.gateway.create_override(
                                plan.course_id, item.assignment_id, values
                            )
                            if result.get("id") is None:
                                raise CanvasError(
                                    "Canvas created an override but did not return its identifier.",
                                    kind="uncertain_write",
                                )
                            change_record["override_id"] = _id(result.get("id"))
                            change_record["state"] = "applied"
                        else:
                            assert item.override_id is not None and item.before is not None
                            change_record = {
                                "kind": "updated_override",
                                "state": "pending",
                                "course_id": plan.course_id,
                                "assignment_id": item.assignment_id,
                                "section_id": item.section_id,
                                "override_id": item.override_id,
                                "before": _restore_override_values(plan, item),
                                "after": asdict(item.after),
                            }
                            operation["applied"].append(change_record)
                            self.operations.save(operation)
                            self.gateway.update_override(
                                plan.course_id,
                                item.assignment_id,
                                item.override_id,
                                values,
                            )
                            change_record["state"] = "applied"
                        self.operations.save(operation)
                    if plan.restrict_assignments:
                        for assignment_id in assignment_ids:
                            before_visibility = bool(
                                plan.assignment_snapshots[assignment_id].get(
                                    "only_visible_to_overrides", False
                                )
                            )
                            if not before_visibility:
                                change_record = {
                                    "kind": "visibility",
                                    "state": "pending",
                                    "course_id": plan.course_id,
                                    "assignment_id": assignment_id,
                                    "before": before_visibility,
                                    "after": True,
                                }
                                operation["applied"].append(change_record)
                                self.operations.save(operation)
                                self.gateway.set_only_visible_to_overrides(
                                    plan.course_id, assignment_id, True
                                )
                                change_record["state"] = "applied"
                                self.operations.save(operation)
                for plan in batch.plans:
                    self._verify_plan(plan)
                operation["status"] = "verified"
                operation["verified_at"] = _now()
            except Exception as exc:
                uncertain = isinstance(exc, CanvasError) and exc.kind == "uncertain_write"
                applied = any(change.get("state") == "applied" for change in operation["applied"])
                operation["status"] = "recovery_required" if uncertain or applied else "failed"
                operation["uncertain_write"] = uncertain
                operation["error"] = str(exc)
                self.operations.save(operation)
                if operation["status"] == "recovery_required":
                    return self._public_operation(operation)
                raise
            self.operations.save(operation)
            return self._public_operation(operation)

    def _verify_plan(
        self, plan: ChangePlan, differences: list[dict[str, Any]] | None = None
    ) -> None:
        assignment_ids = sorted({item.assignment_id for item in plan.items})
        current = {
            assignment_id: self.gateway.get_assignment(plan.course_id, assignment_id)
            for assignment_id in assignment_ids
        }
        for item in plan.items:
            match = next(
                (
                    row
                    for row in current[item.assignment_id].get("overrides", [])
                    if _id(row.get("course_section_id")) == item.section_id
                ),
                None,
            )
            if match is None or not _same_dates(_dates(match), item.after):
                if differences is None:
                    raise RuntimeError(
                        f"Verification failed for {item.assignment_name} / {item.section_name}."
                    )
                differences.append(
                    {
                        "kind": "dates",
                        "course_id": plan.course_id,
                        "course_name": plan.course_name,
                        "assignment_id": item.assignment_id,
                        "assignment_name": item.assignment_name,
                        "section_id": item.section_id,
                        "section_name": item.section_name,
                        "expected": asdict(item.after),
                        "actual": asdict(_dates(match)) if match else None,
                    }
                )
        for item_id in assignment_ids:
            expected_visibility = plan.restrict_assignments or bool(
                plan.assignment_snapshots[item_id].get("only_visible_to_overrides")
            )
            actual_visibility = bool(current[item_id].get("only_visible_to_overrides"))
            if actual_visibility != expected_visibility:
                if differences is None:
                    raise RuntimeError("Assignment visibility verification failed.")
                differences.append(
                    {
                        "kind": "visibility",
                        "course_id": plan.course_id,
                        "course_name": plan.course_name,
                        "assignment_id": item_id,
                        "assignment_name": str(current[item_id].get("name", item_id)),
                        "section_name": "Everyone audience",
                        "expected": expected_visibility,
                        "actual": actual_visibility,
                    }
                )

    def _check_time_zone(self, plans: list[ChangePlan]) -> None:
        current_zone = self.gateway.get_time_zone()
        if any(plan.canvas_time_zone and plan.canvas_time_zone != current_zone for plan in plans):
            raise ValidationError(
                "The Canvas API user's timezone changed after the preview. Create a new preview."
            )

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
            raise ValidationError("Enter a complete operation ID (32 hexadecimal characters).")
        return self._public_operation(self.operations.get(operation_id))

    def recheck(self, operation_id: str) -> dict[str, Any]:
        """Verify saved plans with Canvas GETs only; never retry a remote write."""
        self.get_operation(operation_id)
        with self._execute_lock:
            operation = self.operations.get(operation_id)
            if operation.get("status") not in {"verified", "recovery_required"}:
                raise ValidationError("Only completed or recovery operations can be rechecked.")
            backup_ids = operation.get("backup_ids") or {
                _id(operation.get("course_id")): operation.get("backup_id")
            }
            if not isinstance(backup_ids, dict) or not backup_ids:
                raise ValidationError("This operation has no saved backups to verify.")
            time_zone = self.gateway.get_time_zone()
            plans: list[ChangePlan] = []
            for course_id, backup_id in backup_ids.items():
                manifest = self.backups.read_manifest(_id(backup_id))
                data = manifest.get("planned_change")
                if (
                    manifest.get("canvas_host") != self.gateway.host_identifier
                    or _id(manifest.get("course_id")) != _id(course_id)
                    or not isinstance(data, dict)
                    or _id(data.get("course_id")) != _id(course_id)
                ):
                    raise ValidationError("The saved backup does not match this Canvas operation.")
                snapshots = data.get("assignment_snapshots")
                if not isinstance(snapshots, dict) or not snapshots:
                    raise ValidationError("The saved preview has no assignment snapshots.")
                self.backups.verify(_id(backup_id), expected_ids=list(snapshots))
                # Old previews predate timezone capture. Use the current API user's zone
                # to interpret their original midnight requests without changing those records.
                plan_zone = data.get("canvas_time_zone") or time_zone
                items = [
                    PlanItem(
                        assignment_id=_id(row["assignment_id"]),
                        assignment_name=row["assignment_name"],
                        section_id=_id(row["section_id"]),
                        section_name=row["section_name"],
                        action=row["action"],
                        override_id=row.get("override_id"),
                        before=_dates(row["before"]) if row.get("before") else None,
                        after=normalize_override_dates(_dates(row["after"]), plan_zone),
                    )
                    for row in data["items"]
                ]
                plans.append(
                    ChangePlan(
                        id=data["id"],
                        scope_id=data["scope_id"],
                        course_id=_id(course_id),
                        course_name=data["course_name"],
                        restrict_assignments=bool(data["restrict_assignments"]),
                        items=items,
                        assignment_snapshots=snapshots,
                        created_at=data["created_at"],
                        canvas_time_zone=plan_zone,
                    )
                )
            error = None
            differences: list[dict[str, Any]] = []
            complete = False
            try:
                for plan in plans:
                    self._verify_plan(plan, differences)
                complete = True
                if differences:
                    error = f"{len(differences)} assignment date/visibility difference(s) remain."
            except RuntimeError as exc:
                error = str(exc)
            checked_at = _now()
            operation.setdefault("verification_checks", []).append(
                {
                    "checked_at": checked_at,
                    "previous_status": operation.get("status"),
                    "previous_error": operation.get("error"),
                    "error": error,
                    "api_user_time_zone": time_zone,
                    "remote_writes": 0,
                    "complete": complete,
                    "difference_count": len(differences),
                }
            )
            operation["status"] = "recovery_required" if error else "verified"
            operation["error"] = error
            operation["verification_differences"] = differences
            operation["verification_complete"] = complete
            if error is None:
                operation["verified_at"] = checked_at
            self.operations.save(operation)
            return self._public_operation(operation)

    def rollback(self, operation_id: str) -> dict[str, Any]:
        with self._execute_lock:
            operation = self.operations.get(operation_id)
            if operation.get("status") not in {"verified", "recovery_required"}:
                raise ValidationError("This operation is not eligible for rollback.")
            if operation.get("backup_ids"):
                return self._rollback_batch(operation)
            before_rows = self.backups.read_before(str(operation["backup_id"]))
            before_by_id = {_id(row["id"]): row for row in before_rows}
            try:
                for change in reversed(operation.get("applied", [])):
                    assignment_id = _id(change["assignment_id"])
                    if change["kind"] == "created_override":
                        override_id = change.get("override_id")
                        if override_id is None:
                            current = self.gateway.get_assignment(
                                _id(operation["course_id"]), assignment_id
                            )
                            match = next(
                                (
                                    row
                                    for row in current.get("overrides", [])
                                    if _id(row.get("course_section_id"))
                                    == _id(change["section_id"])
                                    and _same_dates(_dates(row), DateSet(**change["after"]))
                                ),
                                None,
                            )
                            if match is None:
                                continue
                            override_id = _id(match["id"])
                        self.gateway.delete_override(
                            _id(operation["course_id"]), assignment_id, _id(override_id)
                        )
                    elif change["kind"] == "updated_override":
                        self.gateway.update_override(
                            _id(operation["course_id"]),
                            assignment_id,
                            _id(change["override_id"]),
                            dict(change["before"]),
                        )
                    elif change["kind"] == "visibility":
                        self.gateway.set_only_visible_to_overrides(
                            _id(operation["course_id"]), assignment_id, bool(change["before"])
                        )
                for assignment_id, original in before_by_id.items():
                    current = self.gateway.get_assignment(
                        _id(operation["course_id"]), assignment_id
                    )
                    if _state_fingerprint(current) != _state_fingerprint(original):
                        raise RuntimeError(
                            f"Rollback verification failed for assignment {assignment_id}."
                        )
                operation["status"] = "rolled_back"
                operation["rolled_back_at"] = _now()
            except Exception as exc:
                operation["status"] = "rollback_failed"
                operation["error"] = str(exc)
                self.operations.save(operation)
                raise
            self.operations.save(operation)
            return self._public_operation(operation)

    def _rollback_batch(self, operation: dict[str, Any]) -> dict[str, Any]:
        backup_ids = operation.get("backup_ids")
        if not isinstance(backup_ids, dict):
            raise ValidationError("The batch backup record is invalid.")
        before_by_course: dict[str, dict[str, dict[str, Any]]] = {}
        for course_id, backup_id in backup_ids.items():
            before_rows = self.backups.read_before(_id(backup_id))
            before_by_course[_id(course_id)] = {_id(row["id"]): row for row in before_rows}
        try:
            for change in reversed(operation.get("applied", [])):
                course_id = _id(change["course_id"])
                assignment_id = _id(change["assignment_id"])
                if change["kind"] == "created_override":
                    override_id = change.get("override_id")
                    if override_id is None:
                        current = self.gateway.get_assignment(course_id, assignment_id)
                        match = next(
                            (
                                row
                                for row in current.get("overrides", [])
                                if _id(row.get("course_section_id")) == _id(change["section_id"])
                                and _same_dates(_dates(row), DateSet(**change["after"]))
                            ),
                            None,
                        )
                        if match is None:
                            continue
                        override_id = _id(match["id"])
                    self.gateway.delete_override(course_id, assignment_id, _id(override_id))
                elif change["kind"] == "updated_override":
                    self.gateway.update_override(
                        course_id,
                        assignment_id,
                        _id(change["override_id"]),
                        dict(change["before"]),
                    )
                elif change["kind"] == "visibility":
                    self.gateway.set_only_visible_to_overrides(
                        course_id, assignment_id, bool(change["before"])
                    )
            for course_id, before_by_id in before_by_course.items():
                for assignment_id, original in before_by_id.items():
                    current = self.gateway.get_assignment(course_id, assignment_id)
                    if _state_fingerprint(current) != _state_fingerprint(original):
                        raise RuntimeError(
                            f"Rollback verification failed for course {course_id}, "
                            f"assignment {assignment_id}."
                        )
            operation["status"] = "rolled_back"
            operation["rolled_back_at"] = _now()
        except Exception as exc:
            operation["status"] = "rollback_failed"
            operation["error"] = str(exc)
            self.operations.save(operation)
            raise
        self.operations.save(operation)
        return self._public_operation(operation)

    @staticmethod
    def _public_operation(operation: dict[str, Any]) -> dict[str, Any]:
        return {
            key: operation.get(key)
            for key in (
                "id",
                "course_id",
                "course_name",
                "course_ids",
                "course_names",
                "status",
                "created_at",
                "verified_at",
                "rolled_back_at",
                "backup_id",
                "backup_ids",
                "error",
                "verification_differences",
                "verification_complete",
            )
            if operation.get(key) is not None
        }
