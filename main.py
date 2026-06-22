from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from src.product.job import run_product_job, run_product_preview_job
from src.shipment.job import run_shipment_job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Lingxing customs data jobs.")
    parser.add_argument(
        "--job",
        choices=("shipment", "product", "product-preview"),
        default="shipment",
        help="Job to run. Use product to sync customs_product, or product-preview to export a preview workbook.",
    )
    parser.add_argument("--limit", type=int, default=20, help="Row limit for product-preview job.")
    parser.add_argument(
        "--shipment-time",
        "--ship-date",
        dest="shipment_time",
        default=None,
        help="Shipment date. Omit it to run yesterday and today for shipment jobs.",
    )
    parser.add_argument("--output", help="Output .xlsx path.")
    parser.add_argument(
        "--use-sample-data",
        action="store_true",
        help="Use bundled sample data instead of calling Lingxing API.",
    )
    parser.add_argument(
        "--debug-api",
        action="store_true",
        help="Save Lingxing API timing summaries and failed response snapshots to logs/api_debug.",
    )
    parser.add_argument(
        "--debug-full-api",
        action="store_true",
        help="Save full successful Lingxing API responses. This is slower and mainly for deep troubleshooting.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore Lingxing master-data cache and fetch fresh SKU, supplier, purchaser, and purchase order data.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete current Lingxing master-data cache before running.",
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Write shipment detail rows to MySQL after generating workbook data.",
    )
    parser.add_argument(
        "--product-full-refresh",
        action="store_true",
        help="For --job product, delete customs_product rows first and reload all enabled products.",
    )
    parser.add_argument(
        "--product-start-date",
        help="For --job product, product update_time start date, for example 2026-06-17.",
    )
    parser.add_argument(
        "--product-end-date",
        help="For --job product, product update_time end date, for example 2026-06-18.",
    )
    parser.add_argument(
        "--db-preflight",
        action="store_true",
        help="Check MySQL connection, table columns, unique id index, and row ids without writing data.",
    )
    parser.add_argument("--show-ip", action="store_true", help="Print current public outbound IP for whitelist setup.")
    parser.add_argument("--ip-repeat", type=int, default=1, help="Number of public IP probe rounds for --show-ip.")
    parser.add_argument("--ip-interval", type=float, default=1.0, help="Seconds to wait between --show-ip probe rounds.")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Check whether Lingxing access token can be fetched with current .env credentials.",
    )
    parser.add_argument(
        "--probe-purchase-order",
        help="Probe purchaseOrderList request bodies for one purchase order number and print which one matches.",
    )
    args = parser.parse_args()
    if args.show_ip or args.check_auth or args.probe_purchase_order:
        return args
    args.shipment_time_provided = args.shipment_time is not None
    if len(sys.argv) == 1:
        args.job = "shipment"
        args.shipment_time_provided = False
        args.output = "output\\real-recent.xlsx"
        args.use_sample_data = False
        return args
    if args.job == "product-preview" and (args.write_db or args.db_preflight):
        parser.error("--write-db and --db-preflight are not supported for --job product-preview")
    if args.job == "product" and args.db_preflight:
        parser.error("--db-preflight is only supported for --job shipment")
    if args.job == "product" and not args.write_db:
        parser.error("--job product requires --write-db")
    if args.product_full_refresh and args.job != "product":
        parser.error("--product-full-refresh is only supported for --job product")
    if args.product_full_refresh and not args.write_db:
        parser.error("--product-full-refresh requires --write-db")
    product_date_args = [args.product_start_date, args.product_end_date]
    if any(product_date_args) and args.job != "product":
        parser.error("--product-start-date and --product-end-date are only supported for --job product")
    if any(product_date_args) and not all(product_date_args):
        parser.error("--product-start-date and --product-end-date must be used together")
    if args.product_full_refresh and any(product_date_args):
        parser.error("--product-full-refresh cannot be used with --product-start-date/--product-end-date")
    if args.write_db and args.db_preflight:
        parser.error("--write-db and --db-preflight cannot be used together")
    if args.job == "product-preview" and not args.output:
        parser.error("the following arguments are required unless using --show-ip, --check-auth, or --probe-purchase-order: --output")
    if args.job == "shipment" and not (args.output or args.write_db or args.db_preflight):
        parser.error("the following arguments are required unless using --write-db or --db-preflight: --output")
    return args


def main() -> None:
    args = parse_args()
    if args.show_ip:
        show_public_ips(repeat=args.ip_repeat, interval_seconds=args.ip_interval)
        return
    if args.check_auth:
        check_lingxing_auth()
        return
    if args.probe_purchase_order:
        probe_purchase_order(args.probe_purchase_order)
        return

    if args.use_sample_data:
        print("Using sample data. Omit --use-sample-data to call Lingxing API.")
    if args.debug_api:
        os.environ["LINGXING_DEBUG_DIR"] = "logs/api_debug"
        performance_summary = Path("logs/api_debug/performance_summary.json")
        if performance_summary.exists():
            performance_summary.unlink()
    if args.debug_full_api:
        os.environ["LINGXING_DEBUG_FULL_API"] = "1"

    try:
        if args.job == "product":
            run_product_job(args)
        elif args.job == "product-preview":
            run_product_preview_job(args)
        else:
            run_shipment_job(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def show_public_ips(repeat: int = 1, interval_seconds: float = 1.0) -> None:
    urls = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
        "https://checkip.amazonaws.com",
    ]
    seen_ips: set[str] = set()
    repeat = max(1, repeat)
    for round_index in range(1, repeat + 1):
        print(f"Probe round {round_index}/{repeat}")
        for url in urls:
            try:
                with urlopen(url, timeout=10) as response:
                    ip = response.read().decode("utf-8").strip()
                    seen_ips.add(ip)
                    print(f"  {url}: {ip}")
            except Exception as exc:
                print(f"  {url}: ERROR {exc}")
        if round_index < repeat:
            time.sleep(max(0.0, interval_seconds))
    if seen_ips:
        print("Unique public IPs observed:")
        for ip in sorted(seen_ips):
            print(ip)


def check_lingxing_auth() -> None:
    from src.common.lingxing_client import LingxingClient

    try:
        client = LingxingClient()
        token = client._fetch_access_token()
    except Exception as exc:
        print(f"Lingxing access token: FAILED - {exc}", file=sys.stderr)
        sys.exit(1)
    else:
        print("Lingxing access token: OK")
        print(f"Token prefix: {token[:6]}...")


def probe_purchase_order(purchase_sn: str) -> None:
    from src.common.lingxing_client import LingxingClient, LingxingClientError
    from src.shipment.fetcher import _extract_rows, _first_matching_any, _probe_purchase_order_request_bodies

    endpoint = os.getenv("LINGXING_PURCHASE_ORDER_LIST_ENDPOINT", "/erp/sc/routing/data/local_inventory/purchaseOrderList")
    keys = ("purchase_sn", "purchase_order_no", "po_no", "order_sn", "custom_order_sn", "alibaba_order_sn")
    client = LingxingClient()
    print(f"Probing {endpoint} for {purchase_sn}")
    for index, body in enumerate(_probe_purchase_order_request_bodies(purchase_sn), start=1):
        try:
            response = client.post(endpoint, body)
        except LingxingClientError as exc:
            print(f"{index}. ERROR body={body} error={exc}")
            continue
        rows = _extract_rows(response)
        matched = _first_matching_any(rows, keys, purchase_sn)
        code = response.get("code")
        message = response.get("message") or response.get("msg") or ""
        matched_no = ""
        if matched:
            matched_no = str(next((matched.get(key) for key in keys if matched.get(key)), ""))
        print(
            f"{index}. code={code} rows={len(rows)} matched={'YES' if matched else 'NO'} "
            f"matched_no={matched_no} body={body} message={message}"
        )


if __name__ == "__main__":
    main()
