from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ScopedCourse:
    course: dict[str, Any]
    sections: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanvasScope:
    id: str
    source_name: str
    created_at: str
    courses: tuple[ScopedCourse, ...]
    import_mode: str = "standard"
    skipped_rows: int = 0
    reassessment_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
