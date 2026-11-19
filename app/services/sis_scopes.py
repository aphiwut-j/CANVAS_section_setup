from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.scopes import CanvasScope, ScopedCourse
from app.services.scopes import ScopeError, ScopeImportService


class SisScopeImportService(ScopeImportService):
    """Resolve a dated SIS section report using exact reads only."""

    @staticmethod
    def _sis_id(value: Any, row: int, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ScopeError(f"Row {row}: {field} must be a non-empty SIS ID stored as text.")
        result = value.strip()
        if any(ord(char) < 32 for char in result):
            raise ScopeError(f"Row {row}: {field} contains a control character.")
        return result

    @staticmethod
    def _timestamp(value: Any, row: int, field: str) -> str:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScopeError(f"Row {row}: {field} must be an ISO date and time.") from exc
        if parsed.tzinfo is None:
            raise ScopeError(f"Row {row}: {field} must include a timezone.")
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def import_scope(
        self, filename: str, data: bytes, *, reassessment_only: bool = True
    ) -> CanvasScope:
        raw_rows = self._read_rows(filename, data)
        requested: dict[str, tuple[str, str, str]] = {}
        skipped = 0
        for row_number, row in raw_rows:
            if not {"course_id", "section_id", "start_date", "end_date"}.issubset(row):
                raise ScopeError(
                    "SIS reports require course_id, section_id, start_date, and end_date columns."
                )
            section = self._sis_id(row.get("section_id"), row_number, "section_id")
            lowered = section.casefold()
            if reassessment_only and not ("re" in lowered and "assessment" in lowered):
                skipped += 1
                continue
            course = self._sis_id(row.get("course_id"), row_number, "course_id")
            if str(row.get("status", "active")).strip().casefold() != "active":
                raise ScopeError(f"Row {row_number}: selected section must have active status.")
            start = self._timestamp(row.get("start_date"), row_number, "start_date")
            end = self._timestamp(row.get("end_date"), row_number, "end_date")
            if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
                raise ScopeError(f"Row {row_number}: start_date must be before end_date.")
            values = (course, start, end)
            if section in requested and requested[section] != values:
                raise ScopeError(f"Row {row_number}: conflicting course or dates for {section}.")
            requested[section] = values
        if not requested:
            raise ScopeError(
                "No sections match. Uncheck Re-assessment sections only to include other sections."
            )

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for section, (course, start, end) in requested.items():
            grouped[course].append(
                {"sis_section_id": section, "start_date": start, "end_date": end}
            )
        courses: list[ScopedCourse] = []
        resolved_courses: set[str] = set()
        resolved_sections: set[str] = set()
        for sis_course, sections in grouped.items():
            response = self.gateway.get_course(f"sis_course_id:{sis_course}")
            course_id = self._identifier(response.get("id"), row_number=0, field="course_id")
            if response.get("sis_course_id") != sis_course:
                raise ScopeError(
                    f"Cannot verify SIS course {sis_course}. Check access to SIS information."
                )
            if course_id in resolved_courses:
                raise ScopeError("Different SIS courses resolved to the same Canvas course.")
            resolved_courses.add(course_id)
            resolved: list[dict[str, Any]] = []
            for section_row in sections:
                sis_section = section_row["sis_section_id"]
                current = self.gateway.get_section(f"sis_section_id:{sis_section}")
                section_id = self._identifier(current.get("id"), row_number=0, field="section_id")
                if current.get("sis_section_id") != sis_section:
                    raise ScopeError(
                        f"Cannot verify SIS section {sis_section}. Check SIS permissions."
                    )
                if str(current.get("course_id")) != course_id:
                    raise ScopeError(f"Section {sis_section} does not belong to {sis_course}.")
                if section_id in resolved_sections:
                    raise ScopeError("Different SIS sections resolved to the same Canvas section.")
                resolved_sections.add(section_id)
                resolved.append(
                    {
                        **section_row,
                        "id": section_id,
                        "course_id": course_id,
                        "name": str(current.get("name") or sis_section),
                    }
                )
            courses.append(
                ScopedCourse(
                    course={
                        "id": course_id,
                        "sis_course_id": sis_course,
                        "name": str(response.get("name") or sis_course),
                    },
                    sections=tuple(resolved),
                )
            )
        scope = CanvasScope(
            id=uuid.uuid4().hex,
            source_name=Path(filename).name,
            created_at=datetime.now(UTC).isoformat(),
            courses=tuple(courses),
            import_mode="sis_dates",
            skipped_rows=skipped,
            reassessment_only=reassessment_only,
        )
        self.store.put(scope)
        return scope
