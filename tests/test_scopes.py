from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook

from app.services.canvas.demo import DemoCanvasGateway
from app.services.scopes import ScopeError, ScopeImportService, ScopeStore


class ExactLookupGateway(DemoCanvasGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, str]] = []

    def get_course(self, course_id: str) -> dict[str, Any]:
        self.reads.append(("course", str(course_id)))
        return super().get_course(course_id)

    def get_section(self, section_id: str) -> dict[str, Any]:
        self.reads.append(("section", str(section_id)))
        return super().get_section(section_id)


def importer() -> tuple[ScopeImportService, ExactLookupGateway]:
    gateway = ExactLookupGateway()
    return ScopeImportService(gateway, ScopeStore()), gateway


def test_csv_import_deduplicates_and_uses_only_exact_id_lookups() -> None:
    service, gateway = importer()

    scope = service.import_scope(
        "allowed.csv",
        b"Course ID,Section ID\n42,201\n42,202\n42,201\n",
    )

    assert scope.source_name == "allowed.csv"
    assert [item[0] for item in gateway.reads] == ["course", "section", "section"]
    assert gateway.reads == [("course", "42"), ("section", "201"), ("section", "202")]
    assert [section["id"] for section in scope.courses[0].sections] == ["201", "202"]


def test_csv_import_extracts_ids_from_section_urls() -> None:
    service, gateway = importer()

    scope = service.import_scope(
        "allowed.csv",
        (
            b"section_url\n"
            b"https://demo.canvas.invalid/courses/42/sections/201\n"
            b"https://demo.canvas.invalid/courses/42/sections/202/\n"
        ),
    )

    assert gateway.reads == [("course", "42"), ("section", "201"), ("section", "202")]
    assert [section["id"] for section in scope.courses[0].sections] == ["201", "202"]


def test_xlsx_import_reads_first_sheet_and_preserves_ids() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["course_id", "section_id"])
    sheet.append(["42", "203"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    service, gateway = importer()

    scope = service.import_scope("allowed.xlsx", stream.getvalue())

    assert gateway.reads == [("course", "42"), ("section", "203")]
    assert scope.courses[0].sections[0]["name"] == "Online"


def test_xlsx_import_extracts_ids_from_section_url() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["section_url"])
    sheet.append(["https://demo.canvas.invalid/courses/43/sections/211"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    service, gateway = importer()

    scope = service.import_scope("allowed.xlsx", stream.getvalue())

    assert gateway.reads == [("course", "43"), ("section", "211")]
    assert scope.courses[0].course["id"] == "43"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://other.canvas.invalid/courses/42/sections/201", "configured Canvas host"),
        ("https://demo.canvas.invalid/courses/42/users/201", "must end with"),
        ("not-a-url", "complete HTTPS Canvas URL"),
    ],
)
def test_invalid_section_urls_are_rejected(url: str, message: str) -> None:
    service, gateway = importer()

    with pytest.raises(ScopeError, match=message):
        service.import_scope("allowed.csv", f"section_url\n{url}\n".encode())

    assert gateway.reads == []


def test_url_and_explicit_ids_must_agree() -> None:
    service, gateway = importer()

    with pytest.raises(ScopeError, match="course_id does not match"):
        service.import_scope(
            "allowed.csv",
            (
                b"section_url,course_id,section_id\n"
                b"https://demo.canvas.invalid/courses/42/sections/201,43,201\n"
            ),
        )

    assert gateway.reads == []


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("allowed.csv", b"course,section\n42,201\n", "course_id and section_id"),
        ("allowed.csv", b"course_id,section_id\n42,not-an-id\n", "positive Canvas"),
        ("allowed.xls", b"not-an-excel-file", "Legacy .xls"),
    ],
)
def test_invalid_scope_files_are_rejected(
    filename: str, content: bytes, message: str
) -> None:
    service, gateway = importer()

    with pytest.raises(ScopeError, match=message):
        service.import_scope(filename, content)

    assert gateway.reads == []


def test_section_must_belong_to_the_provided_course() -> None:
    service, gateway = importer()
    gateway._courses.append({"id": "999", "name": "Another course"})

    with pytest.raises(ScopeError, match="does not belong"):
        service.import_scope("allowed.csv", b"course_id,section_id\n999,201\n")

    assert gateway.reads == [("course", "999"), ("section", "201")]


def test_scope_store_rejects_courses_and_sections_outside_file() -> None:
    service, _gateway = importer()
    scope = service.import_scope("allowed.csv", b"course_id,section_id\n42,201\n")

    scoped_course = service.store.course(scope.id, "42")
    assert [section["id"] for section in scoped_course.sections] == ["201"]
    with pytest.raises(ScopeError, match="not included"):
        service.store.course(scope.id, "999")
