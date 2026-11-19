from __future__ import annotations

import pytest

from app.models.plans import DateSet
from app.services.canvas.dates import normalize_override_dates
from app.services.canvas.demo import DemoCanvasGateway


@pytest.mark.parametrize(
    ("value", "zone", "expected"),
    [
        ("2030-12-29T00:00:00+11:00", "Australia/Sydney", "2030-12-29T12:59:59Z"),
        ("2030-12-30T00:00:00+11:00", "Australia/Sydney", "2030-12-30T12:59:59Z"),
        ("2030-12-30T00:00:00Z", "UTC", "2030-12-30T23:59:59Z"),
        ("2030-12-30T00:00:00Z", "Australia/Sydney", "2030-12-30T00:00:00Z"),
        ("2026-10-04T00:00:00+10:00", "Australia/Sydney", "2026-10-04T12:59:59Z"),
        ("2026-04-05T00:00:00+11:00", "Australia/Sydney", "2026-04-05T13:59:59Z"),
        ("2030-12-30T09:15:00+11:00", "Australia/Sydney", "2030-12-30T09:15:00+11:00"),
        ("2030-12-30T00:00:00.001+11:00", "Australia/Sydney", "2030-12-30T00:00:00.001+11:00"),
        (None, "Australia/Sydney", None),
    ],
)
def test_canvas_midnight_rule_uses_api_user_timezone_and_dst(
    value: str | None, zone: str, expected: str | None
) -> None:
    result = normalize_override_dates(DateSet(value, value, value), zone)
    assert result == DateSet(value, expected, expected)


def test_unknown_timezone_is_not_guessed() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalize_override_dates(DateSet(None, None, None), "Not/AZone")


def test_demo_gateway_models_canvas_conversion_on_raw_requests() -> None:
    gateway = DemoCanvasGateway()
    result = gateway.create_override(
        "43",
        "311",
        {
            "course_section_id": "211",
            "unlock_at": "2026-12-20T13:00:00Z",
            "due_at": "2030-12-28T13:00:00Z",
            "lock_at": "2030-12-29T13:00:00Z",
        },
    )
    assert result["due_at"] == "2030-12-29T12:59:59Z"
    assert result["lock_at"] == "2030-12-30T12:59:59Z"
    assert result["unlock_at"] == "2026-12-20T13:00:00Z"
