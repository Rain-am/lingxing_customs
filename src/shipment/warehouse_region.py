from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from src.shipment.models import RawCustomsData
from src.shipment.seller_department import _cell_text, _header_index, _read_xlsx_rows, _row_value


def apply_warehouse_region_mapping(raw_data: RawCustomsData, path: str | None = None) -> tuple[int, int, str | None]:
    mapping_path = _mapping_path(path)
    if not mapping_path.exists():
        _clear_amazon_regions(raw_data)
        return 0, 0, f"warehouse region file not found: {mapping_path}"

    mapping = load_warehouse_region_mapping(mapping_path)
    updated_items = []
    applied_items = 0

    for item in raw_data.shipment_items:
        if item.source != "amazon":
            updated_items.append(item)
            continue
        region = region_for_center(item.logistics_center_code, mapping)
        if region:
            applied_items += 1
        updated_items.append(replace(item, logistics_center_region=region))

    raw_data.shipment_items = updated_items
    return len(mapping), applied_items, None


def load_warehouse_region_mapping(path: Path) -> dict[str, str]:
    rows = _read_xlsx_rows(path, preferred_sheet_name="Sheet2")
    headers = [str(value or "").strip() for value in (rows[0] if rows else [])]
    region_index = _header_index(headers, "分区")
    warehouse_index = _header_index(headers, "仓库")
    if region_index is None or warehouse_index is None:
        raise RuntimeError("warehouse region Excel must contain headers: 分区, 仓库")

    mapping: dict[str, str] = {}
    for row in rows[1:]:
        warehouse = _normalize_center_code(_cell_text(_row_value(row, warehouse_index)))
        if not warehouse:
            continue
        mapping[warehouse] = _cell_text(_row_value(row, region_index))
    return mapping


def region_for_center(center_code: str, region_by_center: dict[str, str]) -> str:
    if not center_code:
        return ""
    return region_by_center.get(_normalize_center_code(center_code), "")


def _clear_amazon_regions(raw_data: RawCustomsData) -> None:
    raw_data.shipment_items = [
        replace(item, logistics_center_region="") if item.source == "amazon" else item
        for item in raw_data.shipment_items
    ]


def _mapping_path(path: str | None) -> Path:
    if path:
        return Path(path)
    configured = os.getenv("SHIPMENT_WAREHOUSE_REGION_FILE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "仓库-分区.xlsx"


def _normalize_center_code(value: str) -> str:
    return str(value or "").strip().upper()
