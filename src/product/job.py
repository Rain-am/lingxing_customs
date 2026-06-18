from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.product.export_mysql import export_products_to_mysql
from src.product.fetcher import ProductApiDataSource


def run_product_job(args: Any) -> None:
    data_source = ProductApiDataSource()
    product_full_refresh = bool(getattr(args, "product_full_refresh", False))
    start_date = None
    end_date = None
    if product_full_refresh:
        print("Product sync mode: full refresh")
    else:
        start_date, end_date = _default_incremental_window()
        print("Product sync mode: incremental")
        print(f"Product update_time window: {start_date} ~ {end_date}")
    rows = data_source.load_all(start_date=start_date, end_date=end_date)
    stats = data_source.stats
    print(f"Product list raw rows: {stats.product_list_raw_rows}")
    print(f"Product list rows in update_time window: {stats.product_list_rows}")
    print(f"Product rows without id skipped: {stats.products_without_id}")
    print(f"Enabled product rows: {stats.enabled_products}")
    print(f"Non-enabled product rows skipped: {stats.skipped_not_enabled}")
    print(f"Batch product detail requests: {stats.detail_request_count}")
    print(f"Product detail missing rows: {stats.detail_missing}")
    print(f"Product empty status rows: {stats.empty_status_rows}")
    if stats.status_counts:
        status_parts = [
            f"{status}={count}"
            for status, count in sorted(stats.status_counts.items(), key=lambda item: item[1], reverse=True)[:10]
        ]
        print("Product status values: " + ", ".join(status_parts))
    print(f"Product rows fetched: {len(rows)}")
    if not args.write_db:
        raise RuntimeError("Product job requires --write-db.")
    result = export_products_to_mysql(rows, full_refresh=product_full_refresh)
    print(f"MySQL target table: {result.table}")
    print(f"MySQL product rows deleted: {result.deleted_rows}")
    print(f"MySQL product rows total: {result.total_rows}")
    print(f"MySQL product rows inserted: {result.inserted_rows}")
    print(f"MySQL product rows updated: {result.updated_rows}")
    print(f"MySQL product rows skipped: {result.skipped_rows}")
    _print_api_performance_summary()


def _default_incremental_window() -> tuple[str, str]:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return yesterday.isoformat(), today.isoformat()


def run_product_preview_job(args: Any) -> None:
    from datetime import datetime
    from pathlib import Path

    from src.product.export_excel import export_product_preview_workbook

    rows = ProductApiDataSource().load_preview(limit=args.limit)
    output_path = Path(args.output)
    try:
        export_product_preview_workbook(rows, output_path)
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = output_path.with_name(f"{output_path.stem}-{timestamp}{output_path.suffix}")
        export_product_preview_workbook(rows, output_path)
        print(f"Output file is in use, wrote a new file instead: {output_path}")

    print(f"Generated product preview workbook: {output_path.resolve()}")
    print(f"Product preview rows: {len(rows)}")
    _print_api_performance_summary()


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
