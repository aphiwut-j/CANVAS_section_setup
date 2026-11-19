from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.models.plans import ChangePlan


@dataclass(frozen=True)
class BatchChangePlan:
    id: str
    scope_id: str
    plans: tuple[ChangePlan, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        courses: list[dict[str, Any]] = []
        for plan in self.plans:
            courses.append(
                {
                    "course_id": plan.course_id,
                    "course_name": plan.course_name,
                    "item_count": len(plan.items),
                }
            )
            for item in plan.items:
                items.append(
                    {
                        **asdict(item),
                        "course_id": plan.course_id,
                        "course_name": plan.course_name,
                    }
                )
            for message in plan.warnings:
                if message.startswith(("Canvas date interpretation", "Canvas converts midnight")):
                    if message not in warnings:
                        warnings.append(message)
                else:
                    warnings.append(f"{plan.course_name}: {message}")
        return {
            "id": self.id,
            "scope_id": self.scope_id,
            "created_at": self.created_at,
            "courses": courses,
            "items": items,
            "warnings": warnings,
        }
