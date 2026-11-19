from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class DateSet:
    unlock_at: str | None
    due_at: str | None
    lock_at: str | None


@dataclass(frozen=True)
class PlanItem:
    assignment_id: str
    assignment_name: str
    section_id: str
    section_name: str
    action: Literal["create", "update", "no_change"]
    override_id: str | None
    before: DateSet | None
    after: DateSet


@dataclass
class ChangePlan:
    id: str
    scope_id: str
    course_id: str
    course_name: str
    restrict_assignments: bool
    items: list[PlanItem]
    assignment_snapshots: dict[str, dict[str, Any]]
    created_at: str
    warnings: list[str] = field(default_factory=list)
    canvas_time_zone: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
