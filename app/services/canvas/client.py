from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote, urlparse

import requests
from requests import Response


class CanvasError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "canvas_api", status: int = 502) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


class UnconfiguredCanvasGateway:
    host_identifier = "not-configured"

    def __init__(self, message: str) -> None:
        self.message = message

    def _raise(self) -> None:
        raise CanvasError(self.message, kind="configuration", status=503)

    def get_course(self, course_id: str) -> dict[str, Any]:
        self._raise()
        return {}

    def get_time_zone(self) -> str:
        self._raise()
        return ""

    def get_section(self, section_id: str) -> dict[str, Any]:
        self._raise()
        return {}

    def list_assignments(self, course_id: str) -> list[dict[str, Any]]:
        self._raise()
        return []

    def get_assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        self._raise()
        return {}

    def create_override(
        self, course_id: str, assignment_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        self._raise()
        return {}

    def update_override(
        self,
        course_id: str,
        assignment_id: str,
        override_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        self._raise()
        return {}

    def delete_override(
        self, course_id: str, assignment_id: str, override_id: str
    ) -> dict[str, Any]:
        self._raise()
        return {}

    def set_only_visible_to_overrides(
        self, course_id: str, assignment_id: str, enabled: bool
    ) -> dict[str, Any]:
        self._raise()
        return {}


class CanvasClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 20.0) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized or not token:
            raise CanvasError(
                "Canvas is not configured. Set CANVAS_BASE_URL and CANVAS_API_TOKEN.",
                kind="configuration",
                status=503,
            )
        if urlparse(normalized).scheme != "https":
            raise CanvasError("CANVAS_BASE_URL must use HTTPS.", kind="configuration", status=503)
        self.base_url = normalized
        self.host_identifier = urlparse(normalized).netloc
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json+canvas-string-ids",
                "User-Agent": "CanvasSectionPlanner/0.1",
            }
        )

    def _safe_message(self, response: Response) -> str:
        if response.status_code in {401, 403}:
            return "Canvas rejected the request. Check the credential and course permissions."
        if response.status_code == 429:
            return "Canvas rate-limited the request. Please wait and try again."
        return f"Canvas returned HTTP {response.status_code}."

    def _request(
        self,
        method: str,
        url_or_path: str,
        *,
        params: list[tuple[str, Any]] | None = None,
        data: list[tuple[str, Any]] | None = None,
    ) -> Response:
        url = url_or_path if url_or_path.startswith("https://") else f"{self.base_url}{url_or_path}"
        if urlparse(url).netloc != self.host_identifier:
            raise CanvasError("Canvas pagination returned an unexpected host.")
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method, url, params=params, data=data, timeout=self.timeout
                )
            except requests.Timeout as exc:
                if method != "GET" or attempt == attempts - 1:
                    kind = "uncertain_write" if method != "GET" else "network"
                    raise CanvasError(
                        "The Canvas request timed out. "
                        "The remote state must be re-read before retrying.",
                        kind=kind,
                    ) from exc
                time.sleep(0.4 * (2**attempt))
                continue
            except requests.RequestException as exc:
                if method != "GET" or attempt == attempts - 1:
                    kind = "uncertain_write" if method != "GET" else "network"
                    raise CanvasError("Canvas could not be reached.", kind=kind) from exc
                time.sleep(0.4 * (2**attempt))
                continue
            if response.status_code == 429 and method == "GET" and attempt < attempts - 1:
                retry_after = min(float(response.headers.get("Retry-After", "1")), 5.0)
                time.sleep(retry_after)
                continue
            if not response.ok:
                kind = "authorization" if response.status_code in {401, 403} else "canvas_api"
                if response.status_code == 429:
                    kind = "rate_limit"
                raise CanvasError(
                    self._safe_message(response), kind=kind, status=response.status_code
                )
            return response
        raise CanvasError("Canvas request failed.")

    def _json(self, response: Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise CanvasError("Canvas returned an invalid response.") from exc

    def _get_all(
        self, path: str, params: list[tuple[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        query = list(params or []) + [("per_page", 100)]
        response = self._request("GET", path, params=query)
        items: list[dict[str, Any]] = []
        while True:
            page = self._json(response)
            if not isinstance(page, list):
                raise CanvasError("Canvas returned an unexpected collection response.")
            items.extend(page)
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return items
            response = self._request("GET", next_url)

    def get_time_zone(self) -> str:
        # Retain only the timezone, never the profile's personal data/calendar URLs.
        response = self._request("GET", "/api/v1/users/self/profile")
        profile = self._json(response)
        zone = profile.get("time_zone") if isinstance(profile, dict) else None
        if not isinstance(zone, str) or not zone.strip():
            raise CanvasError(
                "Canvas did not provide the API user's timezone. "
                "Check profile access and timezone.",
                kind="configuration",
                status=503,
            )
        return zone.strip()

    def get_course(self, course_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/api/v1/courses/{quote(course_id, safe=':')}")
        result = self._json(response)
        if not isinstance(result, dict):
            raise CanvasError("Canvas returned an unexpected course response.")
        return result

    def get_section(self, section_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/api/v1/sections/{quote(section_id, safe=':')}")
        result = self._json(response)
        if not isinstance(result, dict):
            raise CanvasError("Canvas returned an unexpected section response.")
        return result

    def list_assignments(self, course_id: str) -> list[dict[str, Any]]:
        return self._get_all(
            f"/api/v1/courses/{course_id}/assignments",
            [("include[]", "overrides"), ("include[]", "all_dates"), ("order_by", "name")],
        )

    def get_assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            params=[("include[]", "overrides"), ("all_dates", "true")],
        )
        result = self._json(response)
        if not isinstance(result, dict):
            raise CanvasError("Canvas returned an unexpected assignment response.")
        return result

    @staticmethod
    def _override_form(values: dict[str, Any]) -> list[tuple[str, Any]]:
        return [
            (f"assignment_override[{key}]", "" if value is None else value)
            for key, value in values.items()
        ]

    def create_override(
        self, course_id: str, assignment_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides",
            data=self._override_form(values),
        )
        result = self._json(response)
        return result if isinstance(result, dict) else {}

    def update_override(
        self,
        course_id: str,
        assignment_id: str,
        override_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._request(
            "PUT",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides/{override_id}",
            data=self._override_form(values),
        )
        result = self._json(response)
        return result if isinstance(result, dict) else {}

    def delete_override(
        self, course_id: str, assignment_id: str, override_id: str
    ) -> dict[str, Any]:
        response = self._request(
            "DELETE",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides/{override_id}",
        )
        result = self._json(response)
        return result if isinstance(result, dict) else {}

    def set_only_visible_to_overrides(
        self, course_id: str, assignment_id: str, enabled: bool
    ) -> dict[str, Any]:
        response = self._request(
            "PUT",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            data=[("assignment[only_visible_to_overrides]", "true" if enabled else "false")],
        )
        result = self._json(response)
        return result if isinstance(result, dict) else {}
