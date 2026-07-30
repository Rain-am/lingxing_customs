from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from src.common.cache import JsonCache
from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.models import PurchaseBatch, RawCustomsData, ShipmentItem, SkuInfo
from src.shipment.shop_mapping import (
    ShopMappingClient,
    ShopMappingConfig,
    ShopMappingError,
    ShopMappingRecord,
    apply_shop_mapping,
    generate_sign,
    load_shop_mapping_for_current_slot,
    shop_mapping_slot_key,
)


class ShopMappingTest(unittest.TestCase):
    def test_generate_sign_matches_document_sample(self) -> None:
        self.assertEqual(
            generate_sign("abcdefgh", {"timestamp": "1619064310086"}),
            "733122714903D797E2AF29D81634107D",
        )

    def test_client_fetches_pages_and_last_duplicate_shop_wins(self) -> None:
        calls = []

        def fake_urlopen(request, timeout):
            payload = json.loads((request.data or b"{}").decode("utf-8"))
            calls.append((payload, request.headers, timeout))
            if payload["pageIndex"] == 1:
                return FakeResponse(
                    {
                        "success": True,
                        "code": "200",
                        "data": [
                            {"shop_name": " SHOP-A ", "ywbm": "业务一部", "cgzt": "采购主体A", "zzkh": "客户A"},
                            {"shop_name": "SHOP-B", "ywbm": "业务二部", "cgzt": "采购主体B", "zzkh": "客户B"},
                        ],
                    }
                )
            return FakeResponse(
                {
                    "success": True,
                    "code": "200",
                    "data": [
                        {"shop_name": "SHOP-A", "ywbm": "业务三部", "cgzt": "采购主体A2", "zzkh": "客户A2"},
                    ],
                }
            )

        config = ShopMappingConfig(
            url="http://127.0.0.1/api/assetOpenApi/queryData",
            app_key="app-key",
            app_secret="app-secret",
            api_id="aOeOfPzADG",
            page_size=2,
            timeout_seconds=5,
            max_retries=1,
        )

        with patch("src.shipment.shop_mapping.urlopen", fake_urlopen):
            mapping = ShopMappingClient(config).fetch_all()

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], {"apiId": "aOeOfPzADG", "pageIndex": 1, "pageSize": 2})
        self.assertEqual(calls[0][2], 5)
        self.assertEqual(mapping["SHOP-A"].department, "业务三部")
        self.assertEqual(mapping["SHOP-A"].purchase_entity, "采购主体A2")
        self.assertEqual(mapping["SHOP-A"].final_customer, "客户A2")
        self.assertEqual(mapping["SHOP-B"].purchase_entity, "采购主体B")

    def test_slot_key_uses_10_and_14_windows(self) -> None:
        self.assertEqual(shop_mapping_slot_key(datetime(2026, 7, 30, 9, 59)), "2026-07-29-1400")
        self.assertEqual(shop_mapping_slot_key(datetime(2026, 7, 30, 10, 0)), "2026-07-30-1000")
        self.assertEqual(shop_mapping_slot_key(datetime(2026, 7, 30, 13, 59)), "2026-07-30-1000")
        self.assertEqual(shop_mapping_slot_key(datetime(2026, 7, 30, 14, 0)), "2026-07-30-1400")

    def test_current_slot_cache_refreshes_once_then_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = JsonCache(cache_dir=Path(tmpdir))
            client = FakeShopMappingClient(
                {
                    "SHOP-A": ShopMappingRecord(
                        shop_name="SHOP-A",
                        department="业务一部",
                        purchase_entity="采购主体A",
                        final_customer="客户A",
                    )
                }
            )

            first = load_shop_mapping_for_current_slot(datetime(2026, 7, 30, 10, 5), client=client, cache=cache)
            second = load_shop_mapping_for_current_slot(datetime(2026, 7, 30, 11, 5), client=client, cache=cache)

        self.assertTrue(first.refreshed)
        self.assertEqual(first.source, "api")
        self.assertEqual(second.source, "slot-cache")
        self.assertEqual(client.call_count, 1)
        self.assertEqual(second.mapping["SHOP-A"].final_customer, "客户A")

    def test_refresh_failure_uses_latest_cache_and_does_not_retry_same_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = JsonCache(cache_dir=Path(tmpdir))
            first_client = FakeShopMappingClient(
                {
                    "SHOP-A": ShopMappingRecord(
                        shop_name="SHOP-A",
                        purchase_entity="采购主体A",
                        final_customer="客户A",
                    )
                }
            )
            load_shop_mapping_for_current_slot(datetime(2026, 7, 30, 10, 5), client=first_client, cache=cache)

            failing_client = FakeShopMappingClient({}, error=ShopMappingError("api down"))
            fallback = load_shop_mapping_for_current_slot(datetime(2026, 7, 30, 14, 5), client=failing_client, cache=cache)
            retry_client = FakeShopMappingClient(
                {
                    "SHOP-B": ShopMappingRecord(
                        shop_name="SHOP-B",
                        purchase_entity="采购主体B",
                        final_customer="客户B",
                    )
                }
            )
            retry = load_shop_mapping_for_current_slot(datetime(2026, 7, 30, 15, 5), client=retry_client, cache=cache)

        self.assertEqual(fallback.source, "latest-cache")
        self.assertIn("using latest cache", fallback.warning or "")
        self.assertEqual(fallback.mapping["SHOP-A"].purchase_entity, "采购主体A")
        self.assertEqual(failing_client.call_count, 1)
        self.assertEqual(retry.source, "latest-cache")
        self.assertEqual(retry_client.call_count, 0)

    def test_apply_mapping_sets_purchase_entity_and_final_customer_by_shop(self) -> None:
        raw = _raw_data()
        mapping = {
            "SHOP-A": ShopMappingRecord(
                shop_name="SHOP-A",
                department="业务一部",
                purchase_entity="接口采购主体",
                final_customer="接口最终客户",
            )
        }

        loaded_rows, applied_rows = apply_shop_mapping(raw, mapping)
        workbook_data = build_customs_workbook_data(raw)
        row = workbook_data.customs_rows[0]

        self.assertEqual(loaded_rows, 1)
        self.assertEqual(applied_rows, 1)
        self.assertEqual(raw.shipment_items[0].purchase_entity, "接口采购主体")
        self.assertEqual(raw.shipment_items[0].final_customer, "接口最终客户")
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "接口采购主体")
        self.assertEqual(row.purchase_entity, "接口采购主体")
        self.assertEqual(row.final_customer, "接口最终客户")
        self.assertFalse(any(issue.field_name == "最终客户" for issue in workbook_data.issue_rows))

    def test_apply_mapping_leaves_unmatched_shop_blank_and_reports_issues(self) -> None:
        raw = _raw_data(seller_name="SHOP-MISSING")

        apply_shop_mapping(raw, {})
        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].purchase_entity, "")
        self.assertEqual(workbook_data.customs_rows[0].final_customer, "")
        self.assertTrue(any(issue.field_name == "采购主体" for issue in workbook_data.issue_rows))
        self.assertTrue(any(issue.field_name == "最终客户" for issue in workbook_data.issue_rows))


def _raw_data(seller_name: str = "SHOP-A") -> RawCustomsData:
    return RawCustomsData(
        shipment_items=[
            ShipmentItem(
                shipment_date="2026-07-30",
                shipment_no="SP1",
                sku="SKU1",
                quantity=Decimal("2"),
                seller_name=seller_name,
                box_no="BOX1",
                purchase_unit_price=Decimal("1.23"),
                supplier="Supplier",
                domestic_source="Source",
            )
        ],
        sku_infos={
            "SKU1": SkuInfo(
                sku="SKU1",
                product_name="Product",
                customs_name_cn="Customs",
                unit="pcs",
                gross_weight=Decimal("1"),
                outer_box_size="1*1*1",
            )
        },
        purchase_batches=[
            PurchaseBatch(
                shipment_no="SP1",
                sku="SKU1",
                box_no="BOX1",
                quantity=Decimal("2"),
                purchase_entity="Old Purchaser",
                supplier="Supplier",
                domestic_source="Source",
                purchase_order_no="PO1",
                purchase_sn="PO1",
                purchase_unit_price=Decimal("1.23"),
            )
        ],
    )


class FakeShopMappingClient:
    def __init__(self, mapping: dict[str, ShopMappingRecord], error: Exception | None = None) -> None:
        self.mapping = mapping
        self.error = error
        self.call_count = 0

    def fetch_all(self) -> dict[str, ShopMappingRecord]:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return self.mapping


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
