from __future__ import annotations

from typing import Any

import pytest

from app.services.canvas.client import CanvasClient, CanvasError


class FakeResponse:
    def __init__(self, value: Any, *, next_url: str | None = None) -> None:
        self._value = value
        self.ok = True
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.links = {"next": {"url": next_url}} if next_url else {}

    def json(self) -> Any:
        return self._value


def test_assignment_pagination_follows_opaque_next_link_and_keeps_token_in_header(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    client = CanvasClient("https://canvas.example.edu", "secret-token")
    calls: list[tuple[str, str, Any]] = []
    responses = [
        FakeResponse(
            [{"id": "1"}], next_url="https://canvas.example.edu/api/v1/courses?page=opaque"
        ),
        FakeResponse([{"id": "2"}]),
    ]

    def fake_request(method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((method, url, kwargs.get("params")))
        return responses.pop(0)

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.list_assignments("42")

    assert [row["id"] for row in result] == ["1", "2"]
    assert calls[1][1].endswith("page=opaque")
    assert all("access_token" not in str(call) for call in calls)
    assert client.session.headers["Authorization"] == "Bearer secret-token"


def test_sis_lookup_escapes_special_characters(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = CanvasClient("https://canvas.example.edu", "secret-token")
    urls: list[str] = []

    def fake_request(method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        assert method == "GET"
        urls.append(url)
        return FakeResponse({"id": "42"})

    monkeypatch.setattr(client.session, "request", fake_request)
    client.get_course("sis_course_id:26-T6 A/1?#")
    client.get_section("sis_section_id:Re-Assessment (E)")
    assert urls == [
        "https://canvas.example.edu/api/v1/courses/sis_course_id:26-T6%20A%2F1%3F%23",
        "https://canvas.example.edu/api/v1/sections/sis_section_id:Re-Assessment%20%28E%29",
    ]


def test_timezone_read_keeps_only_the_profile_timezone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = CanvasClient("https://canvas.example.edu", "secret-token")
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        assert method == "GET"
        calls.append(url)
        return FakeResponse({"time_zone": "Australia/Sydney", "name": "Not retained"})

    monkeypatch.setattr(client.session, "request", fake_request)
    assert client.get_time_zone() == "Australia/Sydney"
    assert calls == ["https://canvas.example.edu/api/v1/users/self/profile"]


def test_missing_timezone_stops_instead_of_guessing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = CanvasClient("https://canvas.example.edu", "secret-token")
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: FakeResponse({}))
    with pytest.raises(CanvasError, match="did not provide"):
        client.get_time_zone()
