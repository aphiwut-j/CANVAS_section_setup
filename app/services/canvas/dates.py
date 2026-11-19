from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.plans import DateSet


def normalize_override_dates(dates: DateSet, time_zone: str) -> DateSet:
    """Match CanvasTime.fancy_midnight for due_at and lock_at, to API precision."""
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Canvas returned an unsupported API user timezone.") from exc

    def normalize(value: str | None) -> str | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Assignment dates must include a timezone.")
        local = parsed.astimezone(zone)
        if (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
            return value
        return (
            local.replace(hour=23, minute=59, second=59)
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )

    return DateSet(
        unlock_at=dates.unlock_at, due_at=normalize(dates.due_at), lock_at=normalize(dates.lock_at)
    )
