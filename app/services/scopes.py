from __future__ import annotations

import csv
import io
import re
import threading
import uuid
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openpyxl import load_workbook

from app.models.scopes import CanvasScope, ScopedCourse
from app.services.canvas.gateway import CanvasGateway


class ScopeError(ValueError):
    pass


class ScopeStore:
    def __init__(self) -> None:
        self._scopes: dict[str, CanvasScope] = {}
        self._lock = threading.Lock()

    def put(self, scope: CanvasScope) -> None:
        with self._lock:
            self._scopes[scope.id] = scope

    def get(self, scope_id: str) -> CanvasScope:
        with self._lock:
            try:
                return self._scopes[scope_id]
            except KeyError as exc:
                raise ScopeError("This imported scope has expired. Upload the file again.") from exc

    def course(self, scope_id: str, course_id: str) -> ScopedCourse:
        scope = self.get(scope_id)
        match = next(
            (item for item in scope.courses if str(item.course.get("id")) == str(course_id)),
            None,
        )
        if match is None:
            raise ScopeError("The course is not included in the uploaded scope.")
        return match


class ScopeImportService:
    MAX_FILE_BYTES = 2 * 1024 * 1024
    MAX_UNCOMPRESSED_XLSX_BYTES = 10 * 1024 * 1024
    MAX_ROWS = 500
    MAX_ZIP_MEMBERS = 200
    ID_HEADERS = {"course_id", "section_id"}
    URL_HEADER = "section_url"

    def __init__(self, gateway: CanvasGateway, store: ScopeStore) -> None:
        self.gateway = gateway
        self.store = store

    @staticmethod
    def _header(value: Any) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
        return normalized.strip("_")

    @staticmethod
    def _identifier(value: Any, *, row_number: int, field: str) -> str:
        if isinstance(value, bool) or value is None:
            raise ScopeError(f"Row {row_number}: {field} must be a Canvas numeric ID.")
        if isinstance(value, int):
            result = str(value)
        elif isinstance(value, float):
            if not value.is_integer() or abs(value) > 9_007_199_254_740_991:
                raise ScopeError(
                    f"Row {row_number}: {field} must be stored as text to preserve the ID."
                )
            result = str(int(value))
        else:
            result = str(value).strip()
        if not re.fullmatch(r"[1-9][0-9]*", result):
            raise ScopeError(f"Row {row_number}: {field} must be a positive Canvas numeric ID.")
        return result

    @classmethod
    def _validate_headers(cls, normalized: list[str], *, excel: bool = False) -> None:
        if cls.URL_HEADER in normalized or cls.ID_HEADERS.issubset(normalized):
            return
        location = "first row" if excel else "header row"
        raise ScopeError(
            f"The {location} must contain section_url or both course_id and section_id."
        )

    def _ids_from_url(self, value: Any, *, row_number: int) -> tuple[str, str]:
        raw = str(value or "").strip()
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ScopeError(f"Row {row_number}: section_url must be a complete HTTPS Canvas URL.")
        expected_host = self.gateway.host_identifier.lower()
        if expected_host != "not-configured" and parsed.netloc.lower() != expected_host:
            raise ScopeError(
                f"Row {row_number}: section_url must use the configured Canvas host "
                f"{self.gateway.host_identifier}."
            )
        match = re.fullmatch(r"/?courses/([1-9][0-9]*)/sections/([1-9][0-9]*)/?", parsed.path)
        if match is None:
            raise ScopeError(
                f"Row {row_number}: section_url must end with "
                "/courses/<course_id>/sections/<section_id>."
            )
        return match.group(1), match.group(2)

    def _rows_from_csv(self, data: bytes) -> list[tuple[int, dict[str, Any]]]:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ScopeError("CSV files must use UTF-8 encoding.") from exc
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ScopeError("The file is empty or has no header row.")
        normalized = [self._header(value) for value in reader.fieldnames]
        self._validate_headers(normalized)
        header_map = dict(zip(reader.fieldnames, normalized, strict=True))
        rows: list[tuple[int, dict[str, Any]]] = []
        for row_number, row in enumerate(reader, start=2):
            normalized_row = {
                header_map[key]: value for key, value in row.items() if key is not None
            }
            if not any(str(value or "").strip() for value in normalized_row.values()):
                continue
            rows.append((row_number, normalized_row))
            if len(rows) > self.MAX_ROWS:
                raise ScopeError(f"The file may contain at most {self.MAX_ROWS} data rows.")
        return rows

    def _rows_from_xlsx(self, data: bytes) -> list[tuple[int, dict[str, Any]]]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > self.MAX_ZIP_MEMBERS:
                    raise ScopeError("The Excel file contains too many internal files.")
                if sum(item.file_size for item in members) > self.MAX_UNCOMPRESSED_XLSX_BYTES:
                    raise ScopeError("The expanded Excel file is too large.")
        except zipfile.BadZipFile as exc:
            raise ScopeError("The uploaded Excel file is invalid.") from exc
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise ScopeError("The uploaded Excel file could not be read.") from exc
        try:
            sheet = workbook.worksheets[0]
            iterator = sheet.iter_rows(values_only=True)
            headers = next(iterator, None)
            if headers is None:
                raise ScopeError("The first worksheet is empty.")
            normalized = [self._header(value) for value in headers]
            self._validate_headers(normalized, excel=True)
            rows: list[tuple[int, dict[str, Any]]] = []
            for row_number, values in enumerate(iterator, start=2):
                normalized_row = {
                    normalized[index]: value
                    for index, value in enumerate(values)
                    if index < len(normalized) and normalized[index]
                }
                if not any(value is not None and str(value).strip() for value in values):
                    continue
                rows.append((row_number, normalized_row))
                if len(rows) > self.MAX_ROWS:
                    raise ScopeError(f"The file may contain at most {self.MAX_ROWS} data rows.")
            return rows
        finally:
            workbook.close()

    def _read_rows(self, filename: str, data: bytes) -> list[tuple[int, dict[str, Any]]]:
        if not data:
            raise ScopeError("Choose a non-empty CSV or Excel file.")
        if len(data) > self.MAX_FILE_BYTES:
            raise ScopeError("The file is larger than the 2 MB limit.")
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            raw_rows = self._rows_from_csv(data)
        elif suffix == ".xlsx":
            raw_rows = self._rows_from_xlsx(data)
        else:
            raise ScopeError("Use a .csv or .xlsx file. Legacy .xls files are not supported.")
        if not raw_rows:
            raise ScopeError("The file contains no course and section rows.")
        return raw_rows

    def _parse(self, filename: str, data: bytes) -> list[tuple[str, str]]:
        raw_rows = self._read_rows(filename, data)
        rows: list[tuple[str, str]] = []
        section_courses: dict[str, str] = {}
        for row_number, row in raw_rows:
            section_url = str(row.get(self.URL_HEADER) or "").strip()
            if section_url:
                course_id, section_id = self._ids_from_url(section_url, row_number=row_number)
                provided_course = str(row.get("course_id") or "").strip()
                provided_section = str(row.get("section_id") or "").strip()
                if (
                    provided_course
                    and self._identifier(
                        row.get("course_id"), row_number=row_number, field="course_id"
                    )
                    != course_id
                ):
                    raise ScopeError(f"Row {row_number}: course_id does not match section_url.")
                if (
                    provided_section
                    and self._identifier(
                        row.get("section_id"), row_number=row_number, field="section_id"
                    )
                    != section_id
                ):
                    raise ScopeError(f"Row {row_number}: section_id does not match section_url.")
            else:
                course_id = self._identifier(
                    row.get("course_id"), row_number=row_number, field="course_id"
                )
                section_id = self._identifier(
                    row.get("section_id"), row_number=row_number, field="section_id"
                )
            previous_course = section_courses.get(section_id)
            if previous_course is not None and previous_course != course_id:
                raise ScopeError(
                    f"Section {section_id} is assigned to more than one course in the file."
                )
            section_courses[section_id] = course_id
            if (course_id, section_id) not in rows:
                rows.append((course_id, section_id))
        return rows

    def import_scope(self, filename: str, data: bytes) -> CanvasScope:
        requested = self._parse(filename, data)
        requested_by_course: dict[str, list[str]] = defaultdict(list)
        for course_id, section_id in requested:
            requested_by_course[course_id].append(section_id)
        scoped_courses: list[ScopedCourse] = []
        for course_id, section_ids in requested_by_course.items():
            course_response = self.gateway.get_course(course_id)
            if str(course_response.get("id")) != course_id:
                raise ScopeError(f"Canvas did not return the requested course {course_id}.")
            course = {
                "id": course_id,
                "name": str(course_response.get("name") or f"Course {course_id}"),
                "course_code": str(course_response.get("course_code") or ""),
            }
            sections: list[dict[str, Any]] = []
            for section_id in section_ids:
                section_response = self.gateway.get_section(section_id)
                if str(section_response.get("id")) != section_id:
                    raise ScopeError(f"Canvas did not return the requested section {section_id}.")
                if str(section_response.get("course_id")) != course_id:
                    raise ScopeError(
                        f"Section {section_id} does not belong to course {course_id} in Canvas."
                    )
                sections.append(
                    {
                        "id": section_id,
                        "course_id": course_id,
                        "name": str(section_response.get("name") or f"Section {section_id}"),
                    }
                )
            scoped_courses.append(ScopedCourse(course=course, sections=tuple(sections)))
        scope = CanvasScope(
            id=uuid.uuid4().hex,
            source_name=Path(filename).name,
            created_at=datetime.now(UTC).isoformat(),
            courses=tuple(scoped_courses),
        )
        self.store.put(scope)
        return scope
