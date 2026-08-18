from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.common.lingxing_client import _load_dotenv


TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class CustomsIssueReportConfig:
    enabled: bool
    url: str
    token: str
    timeout_seconds: int = 15

    @classmethod
    def from_env(cls) -> "CustomsIssueReportConfig":
        _load_dotenv()
        return cls(
            enabled=_env_bool(os.getenv("CUSTOMS_ISSUE_REPORT_ENABLED", "0")),
            url=str(os.getenv("CUSTOMS_ISSUE_REPORT_URL", "")).strip(),
            token=str(os.getenv("CUSTOMS_ISSUE_REPORT_TOKEN", "")).strip(),
            timeout_seconds=_env_int(os.getenv("CUSTOMS_ISSUE_REPORT_TIMEOUT_SECONDS", "15"), 15),
        )


def issue_reporting_enabled(config: CustomsIssueReportConfig | None = None) -> bool:
    return (config or CustomsIssueReportConfig.from_env()).enabled


def report_customs_data_issues(
    issues: Sequence[Mapping[str, Any]],
    *,
    replacement_scope: Mapping[str, Any] | None = None,
    replacement_scopes: Sequence[Mapping[str, Any]] | None = None,
    config: CustomsIssueReportConfig | None = None,
    urlopen_func: Any = urlopen,
) -> dict[str, Any] | None:
    config = config or CustomsIssueReportConfig.from_env()
    if not config.enabled:
        return None
    if not config.url or not config.token:
        print("Warning: customs issue reporting enabled but CUSTOMS_ISSUE_REPORT_URL or CUSTOMS_ISSUE_REPORT_TOKEN is missing.")
        return None

    payload: dict[str, Any] = {"issues": list(issues)}
    if replacement_scope is not None:
        payload["replacement_scope"] = dict(replacement_scope)
    if replacement_scopes is not None:
        payload["replacement_scopes"] = [dict(scope) for scope in replacement_scopes]

    request = Request(
        url=config.url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Customs-Issue-Token": config.token,
        },
        method="POST",
    )

    try:
        with urlopen_func(request, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        print(f"Warning: failed to report customs data issues: HTTP {exc.code}: {raw_error or exc.reason}")
        return None
    except (URLError, TimeoutError, OSError) as exc:
        print(f"Warning: failed to report customs data issues: {exc}")
        return None

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        print("Warning: customs issue report response is not valid JSON.")
        return None
    if not isinstance(parsed, dict):
        print("Warning: customs issue report response is not a JSON object.")
        return None

    imported = parsed.get("imported_count", len(issues))
    resolved = parsed.get("resolved_count", 0)
    active = parsed.get("active_count", "?")
    print(f"Customs issue report pushed: imported={imported}, resolved={resolved}, active={active}")
    return parsed


def stable_issue_key(prefix: str, *parts: Any) -> str:
    normalized = "|".join(_normalize_key_part(part) for part in parts)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _normalize_key_part(value: Any) -> str:
    return str(value or "").strip().lower()


def _env_bool(value: str) -> bool:
    return str(value or "").strip().lower() in TRUTHY_VALUES


def _env_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default
