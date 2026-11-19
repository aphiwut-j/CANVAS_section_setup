from __future__ import annotations

from typing import Any, Protocol


class CanvasGateway(Protocol):
    host_identifier: str

    def get_time_zone(self) -> str: ...

    def get_course(self, course_id: str) -> dict[str, Any]: ...

    def get_section(self, section_id: str) -> dict[str, Any]: ...

    def list_assignments(self, course_id: str) -> list[dict[str, Any]]: ...

    def get_assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]: ...

    def create_override(
        self, course_id: str, assignment_id: str, values: dict[str, Any]
    ) -> dict[str, Any]: ...

    def update_override(
        self,
        course_id: str,
        assignment_id: str,
        override_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]: ...

    def delete_override(
        self, course_id: str, assignment_id: str, override_id: str
    ) -> dict[str, Any]: ...

    def set_only_visible_to_overrides(
        self, course_id: str, assignment_id: str, enabled: bool
    ) -> dict[str, Any]: ...
