"""Canonical serialization for persisted workspace baselines."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .domain import WorkspaceIdentity
from .sandbox import ExternalProtection, WorkspaceBaseline, WorkspaceEntry

MAX_WORKSPACE_BASELINE_BYTES = 1024 * 1024


def workspace_baseline_to_json(baseline: WorkspaceBaseline) -> str:
    """Serialize a WorkspaceBaseline into deterministic bounded JSON."""

    if not isinstance(baseline, WorkspaceBaseline):
        raise TypeError("baseline must be a WorkspaceBaseline")
    payload = {
        "schema_version": baseline.schema_version,
        "identity": baseline.identity.model_dump(mode="json"),
        "head_oid_or_unborn": baseline.head_oid_or_unborn,
        "index_digest": baseline.index_digest,
        "workspace_status_digest": baseline.workspace_status_digest,
        "workspace_diff_digest": baseline.workspace_diff_digest,
        "external_dirty_baseline_digest": baseline.external_dirty_baseline_digest,
        "external_protection": baseline.external_protection.value,
        "workspace_entries": [
            {
                "status": entry.status,
                "path": entry.path,
                "file_type": entry.file_type,
                "content_digest": entry.content_digest,
            }
            for entry in baseline.workspace_entries
        ],
        "external_entries": [
            {
                "status": entry.status,
                "path": entry.path,
                "file_type": entry.file_type,
                "content_digest": entry.content_digest,
            }
            for entry in baseline.external_entries
        ],
        "captured_at": baseline.captured_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def workspace_baseline_from_json(value: str) -> WorkspaceBaseline:
    """Rebuild one WorkspaceBaseline from persisted canonical JSON."""

    if not isinstance(value, str):
        raise TypeError("workspace baseline JSON must be a string")
    payload = json.loads(value)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported workspace baseline schema")
    return WorkspaceBaseline(
        identity=WorkspaceIdentity.model_validate(payload["identity"], strict=True),
        head_oid_or_unborn=str(payload["head_oid_or_unborn"]),
        index_digest=str(payload["index_digest"]),
        workspace_status_digest=str(payload["workspace_status_digest"]),
        workspace_diff_digest=str(payload["workspace_diff_digest"]),
        external_dirty_baseline_digest=str(payload["external_dirty_baseline_digest"]),
        external_protection=ExternalProtection(str(payload["external_protection"])),
        workspace_entries=tuple(
            WorkspaceEntry(
                status=str(item["status"]),
                path=str(item["path"]),
                file_type=str(item["file_type"]),
                content_digest=str(item["content_digest"]),
            )
            for item in payload["workspace_entries"]
        ),
        external_entries=tuple(
            WorkspaceEntry(
                status=str(item["status"]),
                path=str(item["path"]),
                file_type=str(item["file_type"]),
                content_digest=str(item["content_digest"]),
            )
            for item in payload["external_entries"]
        ),
        captured_at=datetime.strptime(
            str(payload["captured_at"]),
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc),
        schema_version=int(payload["schema_version"]),
    )


def workspace_baseline_digest(baseline: WorkspaceBaseline | str) -> str:
    """Return a domain-separated SHA-256 digest for one persisted baseline."""

    encoded_json = (
        workspace_baseline_to_json(baseline)
        if isinstance(baseline, WorkspaceBaseline)
        else baseline
    )
    if not isinstance(encoded_json, str):
        raise TypeError("workspace baseline digest input is invalid")
    payload = b"guarded-harness/workspace-baseline/v1\0" + encoded_json.encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "MAX_WORKSPACE_BASELINE_BYTES",
    "workspace_baseline_digest",
    "workspace_baseline_from_json",
    "workspace_baseline_to_json",
]
