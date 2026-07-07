from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.common.cache import JsonCache
from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.export_excel import export_customs_workbook
from src.shipment.export_mysql import export_customs_rows_to_mysql, preflight_customs_rows_mysql
from src.shipment.fetcher import LingxingApiDataSource
from src.shipment.models import RawCustomsData
from src.shipment.overseas_fetcher import OverseasWarehouseApiDataSource
from src.shipment.product_master import apply_product_master_data
from src.shipment.sample_data import SampleDataSource
from src.shipment.seller_department import apply_seller_department_mapping
from src.shipment.warehouse_region import apply_warehouse_region_mapping


def run_shipment_job(args: Any) -> None:
    if args.clear_cache:
        JsonCache().clear()
        print("Lingxing master-data cache cleared.")
    data_sources = _shipment_data_sources(args)
    shipment_times = _shipment_times(args)
    raw_data = _load_raw_data_for_dates(data_sources, shipment_times)
    _apply_product_master_data(raw_data)
    _apply_seller_department_mapping(raw_data)
    _apply_warehouse_region_mapping(raw_data)
    workbook_data = build_customs_workbook_data(raw_data)
    output_path = _export_with_available_path(workbook_data, Path(args.output)) if args.output else None
    if args.db_preflight:
        result = preflight_customs_rows_mysql(workbook_data)
        print("MySQL preflight: OK")
        print(f"MySQL target table: {result.table}")
        print(f"MySQL rows ready: {result.row_count}")
        print(f"MySQL duplicate ids in current batch: {result.duplicate_id_count}")
    if args.write_db:
        result = preflight_customs_rows_mysql(workbook_data)
        print("MySQL preflight: OK")
        print(f"MySQL target table: {result.table}")
        print(f"MySQL rows ready: {result.row_count}")
        db_rows = export_customs_rows_to_mysql(workbook_data)
        print(f"MySQL rows upserted: {db_rows}")

    if output_path:
        print(f"Generated customs workbook: {output_path.resolve()}")
    else:
        print("Generated customs workbook: skipped (--output not provided)")
    print("Shipment dates: " + ", ".join(shipment_times))
    print("Shipment sources: " + ", ".join(_shipment_source_names(args)))
    print(f"Customs rows: {len(workbook_data.customs_rows)}")
    print(f"Issue rows: {len(workbook_data.issue_rows)}")
    print(f"Purchase split rows: {len(workbook_data.purchase_split_rows)}")
    pending_purchase_entities = sum(1 for row in workbook_data.customs_rows if row.purchase_entity == "待确认")
    purchase_entities = sorted({row.purchase_entity for row in workbook_data.customs_rows if row.purchase_entity != "待确认"})
    print(f"Pending purchase entity rows: {pending_purchase_entities}")
    if purchase_entities:
        print("Purchase entities: " + ", ".join(purchase_entities[:10]))
    if not workbook_data.purchase_split_rows:
        print("Warning: no purchase split rows were generated; shipment detail did not provide purchase_items/purchase_sn.")
    _print_api_performance_summary()


def _shipment_times(args: Any) -> list[str]:
    if getattr(args, "shipment_time_provided", False):
        return [args.shipment_time]
    today = _today()
    return [(today - timedelta(days=1)).isoformat(), today.isoformat()]


def _today() -> date:
    return date.today()


def _shipment_data_sources(args: Any) -> list[Any]:
    if args.use_sample_data:
        return [SampleDataSource()]
    if args.shipment_source == "overseas":
        return [OverseasWarehouseApiDataSource(refresh_cache=args.refresh_cache)]
    if args.shipment_source == "all":
        return [
            LingxingApiDataSource(refresh_cache=args.refresh_cache),
            OverseasWarehouseApiDataSource(refresh_cache=args.refresh_cache),
        ]
    return [LingxingApiDataSource(refresh_cache=args.refresh_cache)]


def _shipment_source_names(args: Any) -> list[str]:
    if args.use_sample_data:
        return ["sample"]
    if args.shipment_source == "all":
        return ["amazon", "overseas"]
    return [args.shipment_source]


def _load_raw_data_for_dates(data_sources: list[Any], shipment_times: list[str]) -> RawCustomsData:
    combined = RawCustomsData()
    for data_source in data_sources:
        for shipment_time in shipment_times:
            raw_data = data_source.load(shipment_time=shipment_time)
            combined.shipment_items.extend(raw_data.shipment_items)
            combined.purchase_batches.extend(raw_data.purchase_batches)
            combined.sku_infos.update(raw_data.sku_infos)
            combined.metadata.update(raw_data.metadata)
    return combined


def _apply_product_master_data(raw_data: RawCustomsData) -> None:
    try:
        loaded_rows, applied_rows = apply_product_master_data(raw_data)
    except Exception as exc:
        print(f"Warning: failed to load product master data from customs_product: {exc}")
        return
    print(f"Product master rows loaded from MySQL: {loaded_rows}")
    print(f"Product master rows applied to shipment SKU info: {applied_rows}")


def _apply_seller_department_mapping(raw_data: RawCustomsData) -> None:
    try:
        loaded_rows, applied_rows, warning = apply_seller_department_mapping(raw_data)
    except Exception as exc:
        print(f"Warning: failed to load seller department mapping: {exc}")
        return
    if warning:
        print(f"Warning: {warning}")
    print(f"Seller department rows loaded: {loaded_rows}")
    print(f"Seller department rows applied to shipment items: {applied_rows}")


def _apply_warehouse_region_mapping(raw_data: RawCustomsData) -> None:
    try:
        loaded_rows, applied_rows, warning = apply_warehouse_region_mapping(raw_data)
    except Exception as exc:
        print(f"Warning: failed to load warehouse region mapping: {exc}")
        return
    if warning:
        print(f"Warning: {warning}")
    print(f"Warehouse region rows loaded: {loaded_rows}")
    print(f"Warehouse region rows applied to shipment items: {applied_rows}")


def _export_with_available_path(workbook_data, output_path: Path) -> Path:
    try:
        export_customs_workbook(workbook_data, output_path)
        return output_path
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fallback_path = output_path.with_name(f"{output_path.stem}-{timestamp}{output_path.suffix}")
        export_customs_workbook(workbook_data, fallback_path)
        print(f"Output file is in use, wrote a new file instead: {fallback_path}")
        return fallback_path


def _print_api_performance_summary() -> None:
    debug_dir = os.getenv("LINGXING_DEBUG_DIR", "")
    if not debug_dir:
        return
    summary_path = Path(debug_dir) / "performance_summary.json"
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    print("Lingxing API performance summary:")
    for endpoint, item in sorted(summary.items(), key=lambda pair: float(pair[1].get("total_seconds", 0)), reverse=True)[:10]:
        print(
            f"  {endpoint}: calls={item.get('count', 0)}, "
            f"seconds={float(item.get('total_seconds', 0)):.2f}, "
            f"errors={item.get('error_count', 0)}"
        )
