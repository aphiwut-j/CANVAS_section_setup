from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook

from app.services.canvas.demo import DemoCanvasGateway
from app.services.scopes import ScopeError, ScopeStore
from app.services.sis_scopes import SisScopeImportService


class RecordingSisGateway(DemoCanvasGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, str]] = []

    def get_course(self, course_id: str) -> dict[str, Any]:
        self.reads.append(("course", course_id))
        return super().get_course(course_id)

    def get_section(self, section_id: str) -> dict[str, Any]:
        self.reads.append(("section", section_id))
        return super().get_section(section_id)


HEADER = "course_id,section_id,status,start_date,end_date\n"
DATES = ",active,2026-11-02T00:00:00+11:00,2026-12-27T00:00:00+11:00\n"


def test_default_filter_exact_resolution_dates_and_deduplication() -> None:
    gateway = RecordingSisGateway()
    importer = SisScopeImportService(gateway, ScopeStore())
    row = "DEMO-42,DEMO-201-Re-Assessment" + DATES
    scope = importer.import_scope(
        "report.csv", (HEADER + row + "DEMO-42,DEMO-202-Regular" + DATES + row).encode()
    )
    assert gateway.reads == [
        ("course", "sis_course_id:DEMO-42"),
        ("section", "sis_section_id:DEMO-201-Re-Assessment"),
    ]
    assert scope.skipped_rows == 1
    assert scope.reassessment_only is True
    assert scope.import_mode == "sis_dates"
    section = scope.courses[0].sections[0]
    assert section["id"] == "201"
    assert section["start_date"] == "2026-11-01T13:00:00Z"
    assert section["end_date"] == "2026-12-26T13:00:00Z"


@pytest.mark.parametrize("sis_id", ["reASSESSMENT", "RE-Assessment", "Re_Assessment"])
def test_filter_ignores_case(sis_id: str) -> None:
    gateway = RecordingSisGateway()
    gateway._sections["42"][0]["sis_section_id"] = sis_id
    scope = SisScopeImportService(gateway, ScopeStore()).import_scope(
        "report.csv", (HEADER + "DEMO-42," + sis_id + DATES).encode()
    )
    assert scope.courses[0].sections[0]["id"] == "201"


def test_unchecked_filter_includes_regular_sections_in_xlsx() -> None:
    gateway = RecordingSisGateway()
    book = Workbook()
    sheet = book.active
    sheet.append(HEADER.strip().split(","))
    sheet.append(("DEMO-42,DEMO-202-Regular" + DATES).strip().split(","))
    stream = BytesIO()
    book.save(stream)
    book.close()
    scope = SisScopeImportService(gateway, ScopeStore()).import_scope(
        "report.xlsx", stream.getvalue(), reassessment_only=False
    )
    assert scope.courses[0].sections[0]["id"] == "202"
    assert scope.reassessment_only is False


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (HEADER + "DEMO-42,ordinary" + DATES, "No sections match"),
        ("course_id,section_id\nDEMO-42,Re-assessment\n", "require"),
        (HEADER + "DEMO-42,Re-assessment,active,,2026-12-27T00:00:00Z", "ISO"),
        (HEADER + "DEMO-42,Re-assessment,active,2026-11-02,2026-12-27", "timezone"),
        (HEADER + "DEMO-42,Re-assessment,deleted,2026-11-02Z,2026-12-27Z", "active"),
        (
            HEADER + "DEMO-42,Re-assessment,active,2027-01-01T00:00:00Z,2026-01-01T00:00:00Z",
            "before",
        ),
        (HEADER + "DEMO-42,Re-assessment" + DATES + "DEMO-43,Re-assessment" + DATES, "conflicting"),
    ],
)
def test_invalid_reports_stop_before_canvas_reads(content: str, error: str) -> None:
    gateway = RecordingSisGateway()
    store = ScopeStore()
    with pytest.raises(ScopeError, match=error):
        SisScopeImportService(gateway, store).import_scope("report.csv", content.encode())
    assert gateway.reads == []


def test_sis_resolution_checks_course_ownership() -> None:
    gateway = RecordingSisGateway()
    with pytest.raises(ScopeError, match="does not belong"):
        SisScopeImportService(gateway, ScopeStore()).import_scope(
            "report.csv", (HEADER + "DEMO-43,DEMO-201-Re-Assessment" + DATES).encode()
        )


def test_sis_resolution_requires_visible_matching_sis_id() -> None:
    gateway = RecordingSisGateway()
    original = gateway.get_section

    def hidden(section_id: str) -> dict[str, Any]:
        section = original(section_id)
        section.pop("sis_section_id")
        return section

    gateway.get_section = hidden  # type: ignore[method-assign]
    with pytest.raises(ScopeError, match="SIS permissions"):
        SisScopeImportService(gateway, ScopeStore()).import_scope(
            "report.csv", (HEADER + "DEMO-42,DEMO-201-Re-Assessment" + DATES).encode()
        )
