from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BackupError(RuntimeError):
    pass


class BackupService:
    FORMAT_VERSION = 1

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(".agent/backups")

    @staticmethod
    def _canonical_bytes(value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def create(
        self,
        *,
        operation_id: str,
        host_identifier: str,
        course_id: str,
        before: list[dict[str, Any]],
        planned_change: dict[str, Any],
    ) -> dict[str, Any]:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"{stamp}_{operation_id}"
        directory = self.root / backup_id
        directory.mkdir(parents=True, exist_ok=False)
        before_bytes = self._canonical_bytes(before)
        checksum = hashlib.sha256(before_bytes).hexdigest()
        before_path = directory / "before.json"
        before_path.write_bytes(before_bytes)
        resource_ids = [str(item.get("id")) for item in before]
        manifest = {
            "format_version": self.FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "canvas_host": host_identifier,
            "course_id": str(course_id),
            "method_and_endpoint": "Canvas assignment and assignment-override endpoints",
            "resource_ids": resource_ids,
            "before_sha256": checksum,
            "planned_change": planned_change,
            "rollback_supported": True,
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        (directory / "rollback.md").write_text(
            "# Rollback\n\nUse the operation's rollback control in the web application. "
            "It removes overrides created by the operation, restores updated override dates, "
            "and restores the prior assignment visibility setting.\n",
            encoding="utf-8",
        )
        self.verify(backup_id, expected_ids=resource_ids)
        return manifest

    def verify(self, backup_id: str, *, expected_ids: list[str]) -> None:
        directory = self.root / backup_id
        manifest_path = directory / "manifest.json"
        before_path = directory / "before.json"
        if not manifest_path.is_file() or not before_path.is_file():
            raise BackupError("Backup files were not created.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            before = json.loads(before_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError("Backup files are not valid JSON.") from exc
        if not isinstance(before, list) or not before:
            raise BackupError("Backup state is empty.")
        found_ids = [str(item.get("id")) for item in before]
        if sorted(found_ids) != sorted(expected_ids):
            raise BackupError("Backup resource identifiers do not match the plan.")
        checksum = hashlib.sha256(self._canonical_bytes(before)).hexdigest()
        if checksum != manifest.get("before_sha256"):
            raise BackupError("Backup checksum verification failed.")

    def read_before(self, backup_id: str) -> list[dict[str, Any]]:
        path = self.root / backup_id / "before.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise BackupError("Backup state has an invalid structure.")
        return value

    def read_manifest(self, backup_id: str) -> dict[str, Any]:
        try:
            value = json.loads(
                (self.root / backup_id / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError("The saved backup manifest could not be read.") from exc
        if not isinstance(value, dict):
            raise BackupError("The saved backup manifest is invalid.")
        return value
