from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import threading
from http.client import IncompleteRead
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from Crypto.Cipher import AES


class LingxingClientError(RuntimeError):
    pass


RETRYABLE_ERRORS = (HTTPError, URLError, TimeoutError, ConnectionError, IncompleteRead, ValueError, LingxingClientError)
_DEBUG_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class LingxingConfig:
    base_url: str
    app_id: str
    app_secret: str
    access_token: str
    timeout_seconds: int = 30
    max_retries: int = 3
    page_size: int = 100

    @classmethod
    def from_env(cls) -> "LingxingConfig":
        _load_dotenv()
        return cls(
            base_url=os.getenv("LINGXING_BASE_URL", "https://openapi.lingxing.com").rstrip("/"),
            app_id=os.getenv("LINGXING_APP_ID", ""),
            app_secret=os.getenv("LINGXING_APP_SECRET", ""),
            access_token=os.getenv("LINGXING_ACCESS_TOKEN", ""),
            timeout_seconds=int(os.getenv("LINGXING_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("LINGXING_MAX_RETRIES", "3")),
            page_size=int(os.getenv("LINGXING_PAGE_SIZE", "100")),
        )


class LingxingClient:
    AUTH_GET_TOKEN_ENDPOINT = "/api/auth-server/oauth/access-token"

    def __init__(self, config: LingxingConfig | None = None) -> None:
        self.config = config or LingxingConfig.from_env()
        self._access_token = self.config.access_token or ""
        self._cipher = AES.new(self.config.app_id.encode("utf-8"), AES.MODE_ECB) if self.config.app_id else None

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", endpoint, params=params)

    def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", endpoint, json_body=payload or {})

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.app_id or not self.config.app_secret:
            raise LingxingClientError("Missing LINGXING_APP_ID or LINGXING_APP_SECRET in .env")
        if self._cipher is None:
            raise LingxingClientError("Invalid LINGXING_APP_ID: AES key cannot be initialized")

        if not self._access_token:
            self._access_token = self._fetch_access_token()

        for attempt in range(1, self.config.max_retries + 1):
            try:
                query_params = self._signed_query_params(params or {}, json_body or {})
                data = self._urlopen_json(method, endpoint, query_params, json_body)
                if _is_token_expired_response(data):
                    self._access_token = self._fetch_access_token()
                    query_params = self._signed_query_params(params or {}, json_body or {})
                    data = self._urlopen_json(method, endpoint, query_params, json_body)
                self._raise_for_api_error(data)
                return data
            except RETRYABLE_ERRORS as exc:
                if _is_non_retryable_error(exc):
                    raise LingxingClientError(f"Lingxing API request failed: {method} {endpoint}: {exc}") from exc
                if attempt >= self.config.max_retries:
                    raise LingxingClientError(f"Lingxing API request failed: {method} {endpoint}: {exc}") from exc
                time.sleep(min(2**attempt, 10))

        raise LingxingClientError(f"Lingxing API request failed: {method} {endpoint}")

    def paginate(
        self,
        endpoint: str,
        payload: dict[str, Any],
        list_key: str = "list",
        page_key: str = "page",
        page_size_key: str = "page_size",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            page_payload = dict(payload)
            page_payload[page_key] = page
            page_payload[page_size_key] = self.config.page_size
            data = self.post(endpoint, page_payload)
            items = _extract_list(data, list_key)
            rows.extend(items)
            if len(items) < self.config.page_size:
                return rows
            page += 1

    def _fetch_access_token(self) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                data = self._urlopen_json(
                    "POST",
                    self.AUTH_GET_TOKEN_ENDPOINT,
                    {"appId": self.config.app_id, "appSecret": self.config.app_secret},
                    None,
                )
                self._raise_for_api_error(data)
                token_data = data.get("data", data)
                access_token = token_data.get("access_token") or token_data.get("accessToken")
                if not access_token:
                    raise LingxingClientError("Lingxing token response did not include access_token")
                return str(access_token)
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                if _is_non_retryable_error(exc):
                    break
                if attempt >= self.config.max_retries:
                    break
                time.sleep(min(2**attempt, 10))
        raise LingxingClientError(f"Lingxing access token request failed after retries: {last_error}") from last_error

    def _signed_query_params(self, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        query_params: dict[str, Any] = {
            "app_key": self.config.app_id,
            "access_token": self._access_token,
            "timestamp": int(time.time()),
        }
        query_params.update(params)
        sign_params = dict(query_params)
        sign_params.update(body)
        query_params["sign"] = self._generate_sign(sign_params)
        return query_params

    def _generate_sign(self, params: dict[str, Any]) -> str:
        items = []
        for key in sorted(params):
            value = params[key]
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            items.append(f"{key}={value}")
        canonical = "&".join(items)
        md5hex = hashlib.md5(canonical.encode("utf-8")).hexdigest().upper()
        encrypted = self._cipher.encrypt(_pkcs5_pad(md5hex))
        return base64.b64encode(encrypted).decode("utf-8")

    def _urlopen_json(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        method = method.upper()
        url = f"{self.config.base_url}/{endpoint.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        body_bytes: bytes | None = None
        if json_body is not None:
            body_bytes = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        request = Request(url=url, data=body_bytes, headers={"Content-Type": "application/json"}, method=method)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - started_at
            self._record_debug_summary(endpoint, elapsed, 0, f"HTTP {exc.code}: {raw_error or exc.reason}")
            self._dump_debug_error(endpoint, json_body, f"HTTP {exc.code}: {raw_error or exc.reason}")
            raise LingxingClientError(f"{endpoint} HTTP {exc.code}: {raw_error or exc.reason}") from exc

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise LingxingClientError("Lingxing API response is not a JSON object")
        elapsed = time.perf_counter() - started_at
        row_count = _response_row_count(parsed)
        error = "" if _is_success_response(parsed) else str(parsed.get("message") or parsed.get("msg") or parsed.get("code") or "")
        self._record_debug_summary(endpoint, elapsed, row_count, error)
        if os.getenv("LINGXING_DEBUG_FULL_API", "").strip().lower() in {"1", "true", "yes", "on"}:
            self._dump_debug_response(endpoint, json_body, parsed)
        elif error:
            self._dump_debug_error(endpoint, json_body, error, parsed)
        return parsed

    def _raise_for_api_error(self, data: dict[str, Any]) -> None:
        code = data.get("code")
        if code in (0, "0", "200"):
            return
        message = data.get("message") or data.get("msg") or data
        raise LingxingClientError(str(message))

    def _dump_debug_response(self, endpoint: str, request_body: dict[str, Any] | None, response: dict[str, Any]) -> None:
        debug_dir = os.getenv("LINGXING_DEBUG_DIR", "")
        if not debug_dir or endpoint == self.AUTH_GET_TOKEN_ENDPOINT:
            return
        path = Path(debug_dir)
        path.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", endpoint.strip("/")) or "root"
        index = len(list(path.glob(f"{safe_name}-*.json"))) + 1
        payload = {
            "endpoint": endpoint,
            "request_body": request_body or {},
            "response": response,
        }
        (path / f"{safe_name}-{index:03d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _dump_debug_error(
        self,
        endpoint: str,
        request_body: dict[str, Any] | None,
        error: str,
        response: dict[str, Any] | None = None,
    ) -> None:
        debug_dir = os.getenv("LINGXING_DEBUG_DIR", "")
        if not debug_dir or endpoint == self.AUTH_GET_TOKEN_ENDPOINT:
            return
        path = Path(debug_dir)
        path.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", endpoint.strip("/")) or "root"
        index = len(list(path.glob(f"{safe_name}-error-*.json"))) + 1
        payload = {
            "endpoint": endpoint,
            "request_body": request_body or {},
            "error": error,
        }
        if response is not None:
            payload["response"] = response
        (path / f"{safe_name}-error-{index:03d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_debug_summary(self, endpoint: str, elapsed_seconds: float, row_count: int, error: str = "") -> None:
        debug_dir = os.getenv("LINGXING_DEBUG_DIR", "")
        if not debug_dir or endpoint == self.AUTH_GET_TOKEN_ENDPOINT:
            return
        path = Path(debug_dir)
        path.mkdir(parents=True, exist_ok=True)
        summary_path = path / "performance_summary.json"
        with _DEBUG_WRITE_LOCK:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                summary = {}
            key = endpoint
            item = summary.setdefault(
                key,
                {
                    "count": 0,
                    "total_seconds": 0.0,
                    "max_seconds": 0.0,
                    "row_count": 0,
                    "error_count": 0,
                    "last_error": "",
                },
            )
            item["count"] += 1
            item["total_seconds"] = round(float(item["total_seconds"]) + elapsed_seconds, 6)
            item["max_seconds"] = round(max(float(item["max_seconds"]), elapsed_seconds), 6)
            item["row_count"] += row_count
            if error:
                item["error_count"] += 1
                item["last_error"] = error[:500]
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_list(data: dict[str, Any], list_key: str) -> list[dict[str, Any]]:
    current: Any = data
    for key in ("data", list_key):
        if isinstance(current, dict):
            current = current.get(key)
    if current is None and isinstance(data.get("data"), dict):
        current = data["data"].get("rows") or data["data"].get("items") or data["data"].get("records") or data["data"].get("data")
    if not isinstance(current, list):
        return []
    return [item for item in current if isinstance(item, dict)]


def _response_row_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    rows = _extract_list(data, "list")
    if rows:
        return len(rows)
    for key in ("items", "rows", "records", "data"):
        value = data.get(key)
        count = _response_row_count(value)
        if count:
            return count
    return 0


def _is_success_response(data: dict[str, Any]) -> bool:
    return data.get("code") in (0, "0", "200")


def _is_token_expired_response(data: dict[str, Any]) -> bool:
    code = str(data.get("code") or "")
    message = str(data.get("message") or data.get("msg") or "").lower()
    return code == "2001003" or ("access token" in message and ("expire" in message or "missing" in message))


def _is_non_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "ip not permit",
            "ip address is not allowed",
            "white list",
            "whitelist",
            "permission denied",
            "no permission",
            "forbidden",
            "参数错误",
            "parameter",
        )
    )


def _pkcs5_pad(text: str) -> bytes:
    block_size = AES.block_size
    padding = block_size - len(text) % block_size
    return (text + chr(padding) * padding).encode("utf-8")


def _load_dotenv(path: str | Path = ".env") -> None:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
