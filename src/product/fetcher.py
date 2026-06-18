from __future__ import annotations

import os
from datetime import datetime, time
from typing import Any

from src.common.lingxing_client import LingxingClient, LingxingClientError
from src.product.models import ProductListRow, ProductLoadStats, ProductRow


ENABLED_BATCH_STATUSES = {"\u542f\u7528", "1"}


class ProductApiDataSource:
    def __init__(self, client: LingxingClient | None = None) -> None:
        self.client = client or LingxingClient()
        self.product_list_endpoint = os.getenv(
            "LINGXING_PRODUCT_LIST_ENDPOINT",
            "/erp/sc/routing/data/local_inventory/productList",
        )
        self.batch_product_detail_endpoint = os.getenv(
            "LINGXING_BATCH_PRODUCT_DETAIL_ENDPOINT",
            "/erp/sc/routing/data/local_inventory/batchGetProductInfo",
        )
        self.batch_product_detail_id_field = os.getenv("LINGXING_BATCH_PRODUCT_DETAIL_ID_FIELD", "productIds")
        self.stats = ProductLoadStats()

    def load_all(self, start_date: str | None = None, end_date: str | None = None) -> list[ProductRow]:
        self._validate_config()
        return self._load_rows(self._fetch_product_list_entries(start_date=start_date, end_date=end_date))

    def load_preview(self, limit: int = 20) -> list[ProductRow]:
        self._validate_config()
        return self._load_rows(self._fetch_product_list_entries(limit))

    def _load_rows(self, rows: list[ProductListRow]) -> list[ProductRow]:
        product_rows: list[ProductRow] = []
        rows_with_id = [row for row in rows if row.product_id]
        self.stats.products_without_id = len(rows) - len(rows_with_id)
        if not rows_with_id:
            return []

        detail_by_id: dict[str, dict[str, Any]] = {}
        detail_by_sku: dict[str, dict[str, Any]] = {}
        for product_ids in _chunks([row.product_id for row in rows_with_id], _product_batch_size()):
            for payload in self._fetch_batch_product_details(product_ids):
                product_id = str(_first(payload, "id", "product_id", "productId") or "")
                sku = str(_first(payload, "sku", "seller_sku", "local_sku", "msku") or "")
                if product_id:
                    detail_by_id[product_id] = payload
                if sku:
                    detail_by_sku[sku] = payload
        for row in rows_with_id:
            payload = detail_by_id.get(row.product_id) or detail_by_sku.get(row.sku)
            if not payload:
                self.stats.detail_missing += 1
                continue
            status = row.batch_status or str(_first(payload, "batch_status", "status", "product_status") or "").strip()
            _count_status(self.stats, status)
            is_enabled = 1 if _is_enabled_status(status) else 0
            if is_enabled:
                self.stats.enabled_products += 1
            else:
                self.stats.skipped_not_enabled += 1
            product_rows.append(_map_product_row(row.sku, payload, row.update_time, is_enabled))
        return product_rows

    def _fetch_batch_product_details(self, product_ids: list[str]) -> list[dict[str, Any]]:
        if not product_ids:
            return []
        try:
            self.stats.detail_request_count += 1
            data = self.client.post(self.batch_product_detail_endpoint, {self.batch_product_detail_id_field: product_ids})
            return _extract_rows(data)
        except LingxingClientError:
            if len(product_ids) <= 1:
                raise
            middle = len(product_ids) // 2
            return self._fetch_batch_product_details(product_ids[:middle]) + self._fetch_batch_product_details(product_ids[middle:])

    def _fetch_product_list_entries(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[ProductListRow]:
        entries: list[ProductListRow] = []
        raw_rows = self._fetch_product_list_rows(limit, start_date=start_date, end_date=end_date)
        self.stats.product_list_raw_rows = len(raw_rows)
        for row in raw_rows:
            sku = str(_first(row, "sku", "seller_sku", "local_sku", "msku") or "")
            if not sku:
                continue
            product_id = str(_first(row, "id", "product_id", "productId") or "")
            update_time = _format_update_time(_first(row, "update_time", "updated_at", "gmt_modified", "modify_time"))
            if start_date and end_date and not _is_update_time_in_window(update_time, start_date, end_date):
                continue
            batch_status = str(_first(row, "batch_status", "product_status") or "").strip()
            entries.append(ProductListRow(product_id=product_id, sku=sku, update_time=update_time, batch_status=batch_status))
        self.stats.product_list_rows = len(entries)
        return entries

    def _fetch_product_list_rows(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        page_rows = self._fetch_page_product_list_rows(limit, start_date=start_date, end_date=end_date)
        offset_rows = self._fetch_offset_product_list_rows(limit, start_date=start_date, end_date=end_date)
        if len(offset_rows) > len(page_rows):
            return offset_rows
        return page_rows

    def _fetch_page_product_list_rows(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        page_size = _product_page_size(self.client)
        if limit is not None:
            page_size = min(max(limit, 1), page_size)
        page = 1
        while True:
            payload = _product_list_payload({"page": page, "page_size": page_size}, start_date, end_date)
            data = self.client.post(self.product_list_endpoint, payload)
            items = _extract_rows(data)
            if not items:
                break
            signature = tuple(str(_first(item, "sku", "seller_sku", "local_sku", "msku") or "") for item in items)
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            rows.extend(items)
            if limit is not None and len(rows) >= limit:
                break
            if len(items) < page_size:
                break
            page += 1
            if page > 500:
                break
        return rows[:limit] if limit is not None else rows

    def _fetch_offset_product_list_rows(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        page_size = _product_page_size(self.client)
        if limit is not None:
            page_size = min(max(limit, 1), page_size)
        offset = 0
        for _ in range(500):
            payload = _product_list_payload({"offset": offset, "length": page_size}, start_date, end_date)
            data = self.client.post(self.product_list_endpoint, payload)
            items = _extract_rows(data)
            if not items:
                break
            signature = tuple(str(_first(item, "sku", "seller_sku", "local_sku", "msku") or "") for item in items)
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            rows.extend(items)
            if limit is not None and len(rows) >= limit:
                break
            if len(items) < page_size:
                break
            offset += len(items)
        return rows[:limit] if limit is not None else rows

    def _validate_config(self) -> None:
        missing = []
        if not self.product_list_endpoint:
            missing.append("LINGXING_PRODUCT_LIST_ENDPOINT")
        if not self.batch_product_detail_endpoint:
            missing.append("LINGXING_BATCH_PRODUCT_DETAIL_ENDPOINT")
        if missing:
            raise RuntimeError("Real Lingxing product API endpoints are not configured: " + ", ".join(missing))


def _map_product_row(sku: str, payload: dict[str, Any], update_time: str, is_enabled: int = 0) -> ProductRow:
    clearance = payload.get("clearance")
    if not isinstance(clearance, dict):
        clearance = {}
    return ProductRow(
        sku=str(payload.get("sku") or payload.get("seller_sku") or sku),
        product_name=str(payload.get("product_name") or payload.get("product_name_cn") or payload.get("name") or ""),
        material_cn=str(clearance.get("customs_clearance_material") or ""),
        unit=str(payload.get("unit") or ""),
        customs_name_cn=str(payload.get("bg_customs_export_name") or ""),
        customs_code=str(payload.get("bg_export_hs_code") or ""),
        update_time=update_time,
        is_enabled=is_enabled,
    )


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("data", "list", "items", "rows", "records"):
        value = data.get(key)
        rows = _extract_rows(value)
        if rows:
            return rows
    return []


def _chunks(values: list[str], chunk_size: int) -> list[list[str]]:
    if not values:
        return []
    chunk_size = max(1, chunk_size)
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def _product_list_payload(payload: dict[str, Any], start_date: str | None, end_date: str | None) -> dict[str, Any]:
    if start_date and end_date:
        payload = dict(payload)
        payload.update(_update_time_range_payload(start_date, end_date))
    return payload


def _update_time_range_payload(start_date: str, end_date: str) -> dict[str, int]:
    start_dt = datetime.combine(datetime.fromisoformat(start_date).date(), time.min)
    end_dt = datetime.combine(datetime.fromisoformat(end_date).date(), time.max.replace(microsecond=0))
    return {
        "update_time_start": int(start_dt.timestamp()),
        "update_time_end": int(end_dt.timestamp()),
    }


def _count_status(stats: ProductLoadStats, status: str) -> None:
    key = status if status else "<empty>"
    stats.status_counts[key] = stats.status_counts.get(key, 0) + 1
    if not status:
        stats.empty_status_rows += 1


def _is_enabled_status(status: str) -> bool:
    return str(status or "").strip() in ENABLED_BATCH_STATUSES


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _format_update_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if len(text) >= 19 and text[4:5] == "-" and text[7:8] == "-":
        return text[:19]
    if not text.isdigit():
        return text

    timestamp = int(text)
    if len(text) >= 13:
        timestamp = timestamp // 1000
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return text


def _is_update_time_in_window(update_time: str, start_date: str, end_date: str) -> bool:
    if len(update_time) < 10:
        return False
    update_date = update_time[:10]
    return start_date <= update_date <= end_date


def _client_page_size(client: Any) -> int:
    config = getattr(client, "config", None)
    page_size = getattr(config, "page_size", 100)
    try:
        value = int(page_size)
    except (TypeError, ValueError):
        return 100
    return value if value > 0 else 100


def _product_page_size(client: Any) -> int:
    env_value = os.getenv("LINGXING_PRODUCT_PAGE_SIZE", "")
    if env_value:
        try:
            value = int(env_value)
        except ValueError:
            value = 1000
        return value if value > 0 else 1000
    return max(_client_page_size(client), 1000)


def _product_batch_size() -> int:
    env_value = os.getenv("LINGXING_PRODUCT_BATCH_SIZE", "")
    if env_value:
        try:
            value = int(env_value)
        except ValueError:
            value = 100
        return min(value, 100) if value > 0 else 100
    return 100
