from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.plans import DateSet
from app.services.canvas.dates import normalize_override_dates


class DemoCanvasGateway:
    host_identifier = "demo.canvas.invalid"
    time_zone = "Australia/Sydney"

    def get_time_zone(self) -> str:
        return self.time_zone

    def __init__(self) -> None:
        self._courses = [
            {"id": "42", "name": "2026 Biology", "course_code": "BIO-2026"},
            {"id": "43", "name": "2026 Chemistry", "course_code": "CHEM-2026"},
        ]
        self._sections = {
            "42": [
                {"id": "201", "course_id": "42", "name": "Monday 9:00 AM"},
                {"id": "202", "course_id": "42", "name": "Wednesday 1:00 PM"},
                {"id": "203", "course_id": "42", "name": "Online"},
            ],
            "43": [
                {"id": "211", "course_id": "43", "name": "Tuesday 10:00 AM"},
                {"id": "212", "course_id": "43", "name": "Thursday 2:00 PM"},
            ],
        }
        self._assignments = {
            "42": [
                {
                    "id": "301",
                    "name": "Lab report 1",
                    "published": True,
                    "due_at": "2026-08-24T13:59:00Z",
                    "unlock_at": "2026-08-16T23:00:00Z",
                    "lock_at": "2026-08-26T13:59:00Z",
                    "only_visible_to_overrides": False,
                    "overrides": [
                        {
                            "id": "901",
                            "course_section_id": "201",
                            "title": "Monday 9:00 AM",
                            "unlock_at": "2026-08-16T23:00:00Z",
                            "due_at": "2026-08-24T13:59:00Z",
                            "lock_at": "2026-08-26T13:59:00Z",
                        }
                    ],
                },
                {
                    "id": "302",
                    "name": "Research proposal",
                    "published": False,
                    "due_at": None,
                    "unlock_at": None,
                    "lock_at": None,
                    "only_visible_to_overrides": False,
                    "overrides": [],
                },
                {
                    "id": "303",
                    "name": "Safety quiz",
                    "published": True,
                    "submission_types": ["online_quiz"],
                    "due_at": "2026-08-20T07:00:00Z",
                    "unlock_at": None,
                    "lock_at": "2026-08-20T08:00:00Z",
                    "only_visible_to_overrides": False,
                    "overrides": [],
                },
            ],
            "43": [
                {
                    "id": "311",
                    "name": "Lab report 1",
                    "published": True,
                    "due_at": None,
                    "unlock_at": None,
                    "lock_at": None,
                    "only_visible_to_overrides": False,
                    "overrides": [],
                },
                {
                    "id": "312",
                    "name": "Research proposal",
                    "published": True,
                    "due_at": None,
                    "unlock_at": None,
                    "lock_at": None,
                    "only_visible_to_overrides": False,
                    "overrides": [],
                },
            ],
        }
        for course in self._courses:
            course["sis_course_id"] = f"DEMO-{course['id']}"
        for sections in self._sections.values():
            for section in sections:
                suffix = "Re-Assessment" if section["id"] in {"201", "211"} else "Regular"
                section["sis_section_id"] = f"DEMO-{section['id']}-{suffix}"
        self._next_override_id = 1000

    def get_course(self, course_id: str) -> dict[str, Any]:
        field = "sis_course_id" if course_id.startswith("sis_course_id:") else "id"
        value = course_id.removeprefix("sis_course_id:")
        course = next((row for row in self._courses if str(row[field]) == value), None)
        if course is None:
            raise KeyError("Course not found")
        return deepcopy(course)

    def get_section(self, section_id: str) -> dict[str, Any]:
        field = "sis_section_id" if section_id.startswith("sis_section_id:") else "id"
        value = section_id.removeprefix("sis_section_id:")
        for sections in self._sections.values():
            section = next((row for row in sections if str(row[field]) == value), None)
            if section is not None:
                return deepcopy(section)
        raise KeyError("Section not found")

    def list_assignments(self, course_id: str) -> list[dict[str, Any]]:
        return deepcopy(self._assignments.get(str(course_id), []))

    def _assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        for assignment in self._assignments.get(str(course_id), []):
            if str(assignment["id"]) == str(assignment_id):
                return assignment
        raise KeyError("Assignment not found")

    def get_assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        return deepcopy(self._assignment(course_id, assignment_id))

    def create_override(
        self, course_id: str, assignment_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        assignment = self._assignment(course_id, assignment_id)
        self._next_override_id += 1
        values = self._normalized_values(values)
        created = {"id": str(self._next_override_id), **deepcopy(values)}
        section = next(
            (
                row
                for row in self._sections.get(str(course_id), [])
                if str(row["id"]) == str(values["course_section_id"])
            ),
            None,
        )
        created["course_section_id"] = str(created["course_section_id"])
        created["title"] = section["name"] if section else "Section"
        assignment["overrides"].append(created)
        return deepcopy(created)

    def update_override(
        self,
        course_id: str,
        assignment_id: str,
        override_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        assignment = self._assignment(course_id, assignment_id)
        values = self._normalized_values(values)
        for override in assignment["overrides"]:
            if str(override["id"]) == str(override_id):
                for key in ("unlock_at", "due_at", "lock_at"):
                    if key in values:
                        override[key] = values[key]
                    else:
                        override.pop(key, None)
                return deepcopy(override)
        raise KeyError("Override not found")

    def _normalized_values(self, values: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_override_dates(
            DateSet(values.get("unlock_at"), values.get("due_at"), values.get("lock_at")),
            self.get_time_zone(),
        )
        return {
            key: getattr(normalized, key) if key in {"due_at", "lock_at"} else value
            for key, value in values.items()
        }

    def delete_override(
        self, course_id: str, assignment_id: str, override_id: str
    ) -> dict[str, Any]:
        assignment = self._assignment(course_id, assignment_id)
        for index, override in enumerate(assignment["overrides"]):
            if str(override["id"]) == str(override_id):
                return deepcopy(assignment["overrides"].pop(index))
        raise KeyError("Override not found")

    def set_only_visible_to_overrides(
        self, course_id: str, assignment_id: str, enabled: bool
    ) -> dict[str, Any]:
        assignment = self._assignment(course_id, assignment_id)
        assignment["only_visible_to_overrides"] = enabled
        return deepcopy(assignment)
