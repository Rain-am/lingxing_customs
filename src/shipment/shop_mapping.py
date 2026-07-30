from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.common.cache import JsonCache
from src.common.lingxing_client import _load_dotenv
from src.shipment.models import RawCustomsData


DEFAULT_API_ID = "aOeOfPzADG"
LOCK_TIMEOUT_SECONDS = 600
LOCK_STALE_SECONDS = 900


class ShopMappingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShopMappingConfig:
    url: str
    app_key: str
    app_secret: str
    api_id: str = DEFAULT_API_ID
    page_size: int = 200
    timeout_seconds: int = 30
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "ShopMappingConfig":
        _load_dotenv()
        config = cls(
            url=os.getenv("CUSTOMS_SHOP_MAPPING_URL", ""),
            app_key=os.getenv("CUSTOMS_SHOP_MAPPING_APP_KEY", ""),
            app_secret=os.getenv("CUSTOMS_SHOP_MAPPING_APP_SECRET", ""),
            api_id=os.getenv("CUSTOMS_SHOP_MAPPING_API_ID", DEFAULT_API_ID),
            page_size=_clamp_page_size(os.getenv("CUSTOMS_SHOP_MAPPING_PAGE_SIZE", "200")),
            timeout_seconds=int(os.getenv("CUSTOMS_SHOP_MAPPING_TIMEOUT_SECONDS", "30")),
            max_retries=max(1, int(os.getenv("CUSTOMS_SHOP_MAPPING_MAX_RETRIES", "3"))),
        )
        missing = [
            name
            for name, value in (
                ("CUSTOMS_SHOP_MAPPING_URL", config.url),
                ("CUSTOMS_SHOP_MAPPING_APP_KEY", config.app_key),
                ("CUSTOMS_SHOP_MAPPING_APP_SECRET", config.app_secret),
            )
            if not value
        ]
        if missing:
            raise ShopMappingError("Missing shop mapping API config in .env: " + ", ".join(missing))
        return config


@dataclass(frozen=True)
class ShopMappingRecord:
    shop_name: str
    department: str = ""
    purchase_entity: str = ""
    final_customer: str = ""


@dataclass(frozen=True)
class ShopMappingLoadResult:
    mapping: dict[str, ShopMappingRecord]
    loaded_rows: int
    slot_key: str
    source: str
    refreshed: bool = False
    warning: str | None = None


class ShopMappingClient:
    def __init__(self, config: ShopMappingConfig | None = None) -> None:
        self.config = config or ShopMappingConfig.from_env()

    def fetch_all(self) -> dict[str, ShopMappingRecord]:
        mapping: dict[str, ShopMappingRecord] = {}
        page_index = 1
        while True:
            rows = self._fetch_page(page_index)
            for row in rows:
                record = _record_from_row(row)
                if record is not None:
                    mapping[record.shop_name] = record
            if len(rows) < self.config.page_size:
                return mapping
            page_index += 1
            if page_index > 1000:
                raise ShopMappingError("Shop mapping API pagination exceeded 1000 pages")

    def _fetch_page(self, page_index: int) -> list[dict[str, Any]]:
        payload = {
            "apiId": self.config.api_id,
            "pageIndex": page_index,
            "pageSize": self.config.page_size,
        }
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                data = self._post_json(payload)
                _raise_for_api_error(data)
                return _extract_rows(data)
            except (HTTPError, URLError, TimeoutError, ValueError, ShopMappingError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                time.sleep(min(2**attempt, 10))
        raise ShopMappingError(f"Shop mapping API request failed after retries: {last_error}") from last_error

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "sign": generate_sign(self.config.app_secret, {"timestamp": timestamp}),
            "timestamp": timestamp,
            "appKey": self.config.app_key,
            "Content-Type": "application/json",
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(self.config.url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ShopMappingError("Shop mapping API response is not a JSON object")
        return parsed


def generate_sign(app_secret: str, params: dict[str, str]) -> str:
    sign_text = "".join(key + str(params[key]) for key in sorted(params)).strip() + app_secret
    return hashlib.md5(sign_text.encode("utf-8")).hexdigest().upper()


def load_shop_mapping_for_current_slot(
    now: datetime | None = None,
    client: ShopMappingClient | None = None,
    cache: JsonCache | None = None,
) -> ShopMappingLoadResult:
    now = now or datetime.now()
    cache = cache or JsonCache()
    slot_key = shop_mapping_slot_key(now)
    cached_mapping = _cache_get_mapping(cache, _slot_cache_key(slot_key))
    if cached_mapping is not None:
        return ShopMappingLoadResult(
            mapping=cached_mapping,
            loaded_rows=len(cached_mapping),
            slot_key=slot_key,
            source="slot-cache",
        )

    with _shop_mapping_lock(cache):
        cached_mapping = _cache_get_mapping(cache, _slot_cache_key(slot_key))
        if cached_mapping is not None:
            return ShopMappingLoadResult(
                mapping=cached_mapping,
                loaded_rows=len(cached_mapping),
                slot_key=slot_key,
                source="slot-cache",
            )

        attempt = cache.get("shop_mapping_attempt", slot_key, ttl_days=3650)
        if isinstance(attempt, dict):
            return _latest_or_empty(cache, slot_key, f"shop mapping API was already attempted for slot {slot_key}: {attempt.get('error', '')}")

        try:
            api_client = client or ShopMappingClient()
            mapping = api_client.fetch_all()
        except ShopMappingError as exc:
            if _is_missing_config_error(exc):
                return _latest_or_empty(cache, slot_key, str(exc))
            cache.set(
                "shop_mapping_attempt",
                slot_key,
                {"success": False, "error": str(exc), "attempted_at": now.isoformat(timespec="seconds")},
            )
            return _latest_or_empty(cache, slot_key, str(exc))

        payload = {
            "slot_key": slot_key,
            "refreshed_at": now.isoformat(timespec="seconds"),
            "records": [asdict(record) for record in mapping.values()],
        }
        cache.set("shop_mapping", _slot_cache_key(slot_key), payload)
        cache.set("shop_mapping", "latest", payload)
        cache.set(
            "shop_mapping_attempt",
            slot_key,
            {"success": True, "attempted_at": now.isoformat(timespec="seconds")},
        )
        return ShopMappingLoadResult(
            mapping=mapping,
            loaded_rows=len(mapping),
            slot_key=slot_key,
            source="api",
            refreshed=True,
        )


def apply_shop_mapping(raw_data: RawCustomsData, mapping: dict[str, ShopMappingRecord]) -> tuple[int, int]:
    exact_entities: dict[tuple[str, str, str], str] = {}
    sku_entities: dict[tuple[str, str], str] = {}
    updated_items = []
    applied_items = 0

    for item in raw_data.shipment_items:
        record = mapping.get(_normalize_shop_name(item.seller_name))
        purchase_entity = record.purchase_entity if record is not None else ""
        final_customer = record.final_customer if record is not None else ""
        if record is not None:
            applied_items += 1
        exact_entities[(item.shipment_no, item.sku, item.box_no or "")] = purchase_entity
        sku_entities[(item.shipment_no, item.sku)] = purchase_entity
        updated_items.append(
            _replace_dataclass(
                item,
                purchase_entity=purchase_entity,
                final_customer=final_customer,
            )
        )

    updated_batches = []
    for batch in raw_data.purchase_batches:
        key = (batch.shipment_no, batch.sku, batch.box_no or "")
        fallback_key = (batch.shipment_no, batch.sku)
        purchase_entity = exact_entities.get(key, sku_entities.get(fallback_key, ""))
        updated_batches.append(_replace_dataclass(batch, purchase_entity=purchase_entity))

    raw_data.shipment_items = updated_items
    raw_data.purchase_batches = updated_batches
    return len(mapping), applied_items


def shop_mapping_slot_key(now: datetime) -> str:
    slot_date = now.date()
    slot_hour = 14
    if now.hour >= 14:
        slot_hour = 14
    elif now.hour >= 10:
        slot_hour = 10
    else:
        slot_date = (now - timedelta(days=1)).date()
        slot_hour = 14
    return f"{slot_date.isoformat()}-{slot_hour:02d}00"


def _latest_or_empty(cache: JsonCache, slot_key: str, warning: str) -> ShopMappingLoadResult:
    latest = _cache_get_mapping(cache, "latest")
    if latest is not None:
        return ShopMappingLoadResult(
            mapping=latest,
            loaded_rows=len(latest),
            slot_key=slot_key,
            source="latest-cache",
            warning=f"failed to refresh shop mapping for slot {slot_key}; using latest cache: {warning}",
        )
    return ShopMappingLoadResult(
        mapping={},
        loaded_rows=0,
        slot_key=slot_key,
        source="empty",
        warning=f"failed to refresh shop mapping for slot {slot_key}; no cached mapping available: {warning}",
    )


def _cache_get_mapping(cache: JsonCache, key: str) -> dict[str, ShopMappingRecord] | None:
    payload = cache.get("shop_mapping", key, ttl_days=3650)
    if not isinstance(payload, dict):
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    mapping: dict[str, ShopMappingRecord] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        record = _record_from_row(
            {
                "shop_name": item.get("shop_name"),
                "ywbm": item.get("department"),
                "cgzt": item.get("purchase_entity"),
                "zzkh": item.get("final_customer"),
            }
        )
        if record is not None:
            mapping[record.shop_name] = record
    return mapping


@contextmanager
def _shop_mapping_lock(cache: JsonCache) -> Iterator[None]:
    lock_path = Path(cache.cache_dir) / "locks" / "shop_mapping_refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    fd: int | None = None
    try:
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"pid={os.getpid()} time={time.time()}".encode("utf-8"))
                break
            except FileExistsError:
                if _is_stale_lock(lock_path):
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.time() - started_at > LOCK_TIMEOUT_SECONDS:
                    raise ShopMappingError(f"Timed out waiting for shop mapping refresh lock: {lock_path}")
                time.sleep(0.5)
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _is_stale_lock(lock_path: Path) -> bool:
    try:
        return time.time() - lock_path.stat().st_mtime > LOCK_STALE_SECONDS
    except FileNotFoundError:
        return False


def _slot_cache_key(slot_key: str) -> str:
    return f"slot-{slot_key}"


def _record_from_row(row: dict[str, Any]) -> ShopMappingRecord | None:
    shop_name = _normalize_shop_name(row.get("shop_name"))
    if not shop_name:
        return None
    return ShopMappingRecord(
        shop_name=shop_name,
        department=_cell_text(row.get("ywbm")),
        purchase_entity=_cell_text(row.get("cgzt")),
        final_customer=_cell_text(row.get("zzkh")),
    )


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "list", "items", "rows", "records"):
        rows = _extract_rows(data.get(key))
        if rows:
            return rows
    return []


def _raise_for_api_error(data: dict[str, Any]) -> None:
    success = data.get("success")
    code = data.get("code")
    if success is True or code in (200, "200", 0, "0"):
        return
    message = data.get("msg") or data.get("message") or data
    raise ShopMappingError(str(message))


def _replace_dataclass(instance: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(instance, **changes)


def _normalize_shop_name(value: Any) -> str:
    return str(value or "").strip()


def _cell_text(value: Any) -> str:
    return str(value or "").strip()


def _clamp_page_size(value: str) -> int:
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        return 200
    return min(max(page_size, 1), 200)


def _is_missing_config_error(exc: Exception) -> bool:
    return "Missing shop mapping API config" in str(exc)
