from __future__ import annotations

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.product.fetcher import ProductApiDataSource, _format_update_time, _product_batch_size, _update_time_range_payload
from src.common.lingxing_client import LingxingClientError


class ProductClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(page_size=100)
        self.post_payloads = []

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"},
                        {"id": "102", "sku": "SKU-2", "update_time": "2026-06-02 11:00:00"},
                    ]
                },
            }
        if endpoint.endswith("batchGetProductInfo"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": product_id,
                            "sku": f"SKU-{int(product_id) - 100}",
                            "product_name": f"Product SKU-{int(product_id) - 100}",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        }
                        for product_id in payload["productIds"]
                    ]
                },
            }
        return {"code": 0, "data": {}}


class PagedProductClient(ProductClient):
    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(page_size=1)

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            page = payload.get("page")
            if page == 1:
                rows = [{"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"}]
            elif page == 2:
                rows = [{"id": "102", "sku": "SKU-2", "update_time": "2026-06-02 11:00:00"}]
            else:
                rows = []
            return {"code": 0, "data": {"list": rows}}
        return super().post(endpoint, payload)


class RepeatingPageProductClient(ProductClient):
    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(page_size=1)

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList") and "page" in payload:
            return {"code": 0, "data": {"list": [{"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"}]}}
        if endpoint.endswith("productList") and "offset" in payload:
            offset = payload.get("offset")
            if offset == 0:
                rows = [{"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"}]
            elif offset == 1:
                rows = [{"id": "102", "sku": "PK-4-T-TE-M-CR-S-01", "update_time": "2026-06-02 11:00:00"}]
            else:
                rows = []
            return {"code": 0, "data": {"list": rows}}
        if endpoint.endswith("batchGetProductInfo"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": "101",
                            "sku": "SKU-1",
                            "product_name": "Product SKU-1",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        },
                        {
                            "product_id": "102",
                            "sku": "PK-4-T-TE-M-CR-S-01",
                            "product_name": "Product PK-4-T-TE-M-CR-S-01",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        },
                    ]
                },
            }
        return super().post(endpoint, payload)


class MixedStatusProductClient(ProductClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"},
                        {"id": "102", "sku": "SKU-2", "update_time": "2026-06-02 11:00:00"},
                        {"id": "103", "sku": "SKU-3", "update_time": "2026-06-03 12:00:00", "batch_status": "禁用"},
                        {"sku": "SKU-NO-ID", "update_time": "2026-06-04 13:00:00"},
                    ]
                },
            }
        if endpoint.endswith("batchGetProductInfo"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": "101",
                            "sku": "SKU-1",
                            "product_name": "Product SKU-1",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        },
                        {
                            "product_id": "102",
                            "sku": "SKU-2",
                            "product_name": "Product SKU-2",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "禁用",
                        }
                    ]
                },
            }
        return {"code": 0, "data": {}}


class EmptyStatusProductClient(ProductClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"},
                    ]
                },
            }
        if endpoint.endswith("batchGetProductInfo"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": "101",
                            "sku": "SKU-1",
                            "product_name": "Product SKU-1",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                        }
                    ]
                },
            }
        return {"code": 0, "data": {}}


class SplitBatchProductClient(ProductClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {"id": "101", "sku": "SKU-1", "update_time": "2026-06-01 10:00:00"},
                        {"id": "102", "sku": "SKU-2", "update_time": "2026-06-02 11:00:00"},
                        {"id": "103", "sku": "SKU-3", "update_time": "2026-06-03 12:00:00"},
                        {"id": "104", "sku": "SKU-4", "update_time": "2026-06-04 13:00:00"},
                    ]
                },
            }
        if endpoint.endswith("batchGetProductInfo"):
            product_ids = payload["productIds"]
            if len(product_ids) > 2:
                raise LingxingClientError("内部错误")
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": product_id,
                            "sku": f"SKU-{int(product_id) - 100}",
                            "product_name": f"Product SKU-{int(product_id) - 100}",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        }
                        for product_id in product_ids
                    ]
                },
            }
        return {"code": 0, "data": {}}


class IncrementalWindowProductClient(ProductClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("productList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {"id": "101", "sku": "SKU-OLD", "update_time": "2026-06-10 10:00:00"},
                        {"id": "102", "sku": "SKU-NEW", "update_time": "2026-06-18 11:00:00"},
                    ]
                },
            }
        if endpoint.endswith("batchGetProductInfo"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "product_id": product_id,
                            "sku": "SKU-NEW",
                            "product_name": "Product SKU-NEW",
                            "clearance": {"customs_clearance_material": "Cotton"},
                            "unit": "pcs",
                            "bg_customs_export_name": "Clothing",
                            "bg_export_hs_code": "6109100000",
                            "batch_status": "1",
                        }
                        for product_id in payload["productIds"]
                    ]
                },
            }
        return {"code": 0, "data": {}}


class ProductApiDataSourceTest(unittest.TestCase):
    def test_product_preview_uses_product_list_update_time_and_detail_fields(self) -> None:
        data_source = ProductApiDataSource(client=ProductClient())
        rows = data_source.load_preview(limit=2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].sku, "SKU-1")
        self.assertEqual(rows[0].product_name, "Product SKU-1")
        self.assertEqual(rows[0].material_cn, "Cotton")
        self.assertEqual(rows[0].unit, "pcs")
        self.assertEqual(rows[0].customs_name_cn, "Clothing")
        self.assertEqual(rows[0].customs_code, "6109100000")
        self.assertEqual(rows[0].update_time, "2026-06-01 10:00:00")
        self.assertEqual(data_source.stats.enabled_products, 2)
        self.assertEqual(rows[0].is_enabled, 1)

    def test_load_all_keeps_disabled_products_and_uses_batch_detail(self) -> None:
        client = MixedStatusProductClient()

        rows = ProductApiDataSource(client=client).load_all()

        self.assertEqual([row.sku for row in rows], ["SKU-1", "SKU-2"])
        self.assertEqual([row.is_enabled for row in rows], [1, 0])
        detail_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("batchGetProductInfo")]
        self.assertFalse([payload for endpoint, payload in client.post_payloads if endpoint.endswith("operate/batch")])
        self.assertEqual(detail_payloads, [{"productIds": ["101", "102", "103"]}])

    def test_load_all_tracks_skipped_and_missing_counts(self) -> None:
        data_source = ProductApiDataSource(client=MixedStatusProductClient())

        data_source.load_all()

        self.assertEqual(data_source.stats.product_list_rows, 4)
        self.assertEqual(data_source.stats.products_without_id, 1)
        self.assertEqual(data_source.stats.enabled_products, 1)
        self.assertEqual(data_source.stats.skipped_not_enabled, 1)
        self.assertEqual(data_source.stats.detail_missing, 1)

    def test_load_all_tracks_empty_status_values(self) -> None:
        data_source = ProductApiDataSource(client=EmptyStatusProductClient())

        rows = data_source.load_all()

        self.assertEqual([row.sku for row in rows], ["SKU-1"])
        self.assertEqual(rows[0].is_enabled, 0)
        self.assertEqual(data_source.stats.empty_status_rows, 1)
        self.assertEqual(data_source.stats.status_counts, {"<empty>": 1})

    def test_load_all_fetches_all_product_pages(self) -> None:
        client = PagedProductClient()

        with patch.dict("os.environ", {"LINGXING_PRODUCT_PAGE_SIZE": "1"}):
            rows = ProductApiDataSource(client=client).load_all()

        self.assertEqual([row.sku for row in rows], ["SKU-1", "SKU-2"])
        list_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("productList")]
        page_payloads = [payload for payload in list_payloads if "page" in payload]
        self.assertEqual([payload["page"] for payload in page_payloads], [1, 2, 3])
        detail_payloads = _unique_payloads(
            payload for endpoint, payload in client.post_payloads if endpoint.endswith("batchGetProductInfo")
        )
        self.assertEqual(detail_payloads, [{"productIds": ["101", "102"]}])

    def test_load_all_adds_update_time_timestamps_to_product_list_requests(self) -> None:
        client = ProductClient()

        ProductApiDataSource(client=client).load_all(start_date="2026-06-17", end_date="2026-06-18")

        list_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("productList")]
        self.assertTrue(list_payloads)
        expected_range = _update_time_range_payload("2026-06-17", "2026-06-18")
        for payload in list_payloads:
            self.assertEqual(payload["update_time_start"], expected_range["update_time_start"])
            self.assertEqual(payload["update_time_end"], expected_range["update_time_end"])
            self.assertNotIn("start_date", payload)
            self.assertNotIn("end_date", payload)
            self.assertNotIn("search_field_time", payload)

    def test_load_all_filters_product_list_rows_by_update_time_window_before_details(self) -> None:
        client = IncrementalWindowProductClient()
        data_source = ProductApiDataSource(client=client)

        rows = data_source.load_all(start_date="2026-06-17", end_date="2026-06-18")

        self.assertEqual([row.sku for row in rows], ["SKU-NEW"])
        self.assertEqual(data_source.stats.product_list_raw_rows, 2)
        self.assertEqual(data_source.stats.product_list_rows, 1)
        detail_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("batchGetProductInfo")]
        self.assertEqual(detail_payloads, [{"productIds": ["102"]}])

    def test_load_all_falls_back_to_offset_when_page_repeats(self) -> None:
        client = RepeatingPageProductClient()

        with patch.dict("os.environ", {"LINGXING_PRODUCT_PAGE_SIZE": "1"}):
            rows = ProductApiDataSource(client=client).load_all()

        self.assertEqual([row.sku for row in rows], ["SKU-1", "PK-4-T-TE-M-CR-S-01"])
        offset_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("productList") and "offset" in payload]
        self.assertEqual([payload["offset"] for payload in offset_payloads], [0, 1, 2])

    def test_batch_product_detail_splits_failed_large_batches(self) -> None:
        client = SplitBatchProductClient()

        with patch.dict("os.environ", {"LINGXING_PRODUCT_BATCH_SIZE": "4"}):
            rows = ProductApiDataSource(client=client).load_all()

        self.assertEqual([row.sku for row in rows], ["SKU-1", "SKU-2", "SKU-3", "SKU-4"])
        detail_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("batchGetProductInfo")]
        self.assertEqual(
            detail_payloads,
            [
                {"productIds": ["101", "102", "103", "104"]},
                {"productIds": ["101", "102"]},
                {"productIds": ["103", "104"]},
            ],
        )

    def test_format_update_time_converts_second_timestamp(self) -> None:
        self.assertEqual(_format_update_time("1780985590"), "2026-06-09 14:13:10")

    def test_format_update_time_converts_millisecond_timestamp(self) -> None:
        self.assertEqual(_format_update_time("1780985590000"), "2026-06-09 14:13:10")

    def test_product_batch_size_is_capped_at_lingxing_limit(self) -> None:
        with patch.dict("os.environ", {"LINGXING_PRODUCT_BATCH_SIZE": "500"}):
            self.assertEqual(_product_batch_size(), 100)

    def test_update_time_range_payload_covers_full_local_days(self) -> None:
        payload = _update_time_range_payload("2026-06-17", "2026-06-18")

        self.assertEqual(
            datetime.fromtimestamp(payload["update_time_start"]).strftime("%Y-%m-%d %H:%M:%S"),
            "2026-06-17 00:00:00",
        )
        self.assertEqual(
            datetime.fromtimestamp(payload["update_time_end"]).strftime("%Y-%m-%d %H:%M:%S"),
            "2026-06-18 23:59:59",
        )


def _unique_payloads(payloads):
    unique = []
    seen = set()
    for payload in payloads:
        key = repr(payload)
        if key not in seen:
            seen.add(key)
            unique.append(payload)
    return unique


if __name__ == "__main__":
    unittest.main()
