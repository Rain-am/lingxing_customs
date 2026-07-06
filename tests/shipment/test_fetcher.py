from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace

from src.common.lingxing_client import LingxingClientError
from src.shipment.fetcher import LingxingApiDataSource


WAREHOUSE = "WAREHOUSE"


class FakeClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(page_size=100)
        self.post_payloads = []

    def paginate(self, endpoint, payload):
        return []

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("getInboundShipmentList"):
            page = payload.get("page", 1)
            rows = (
                [
                    {"shipment_sn": "SP1", "pick_time": "2026-06-09", "shipment_time": "2026-06-10", "status": 1, "wname": WAREHOUSE},
                    {"shipment_sn": "SP2", "pick_time": "2026-06-09", "status": 1, "wname": "OTHER"},
                    {"shipment_sn": "SP3", "pick_time": "2026-06-10", "status": 1, "wname": WAREHOUSE},
                ]
                if page == 1
                else []
            )
            return {"code": 0, "data": {"list": rows}}
        return {"code": 0, "data": {"items": [{"sku": "00123", "quantity": 2}]}}


class FailingDetailClient(FakeClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        raise LingxingClientError('HTTP 403: {"message":"Your IP address is not allowed"}')


class EnrichmentClient(FakeClient):
    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("getInboundShipmentList"):
            rows = [{"shipment_sn": "SP1", "pick_time": "2026-06-09", "status": 1, "wname": WAREHOUSE}] if payload.get("page", 1) == 1 else []
            return {"code": 0, "data": {"list": rows}}
        if endpoint.endswith("getInboundShipmentListMwsDetail"):
            return {
                "code": 0,
                "data": {
                    "head_logistics_list": [
                        {
                            "track_list": [
                                {"transport_type_name": "Rail"},
                            ]
                        }
                    ],
                    "items": [
                        {
                            "sku": "00123",
                            "quantity": 2,
                            "sname": "YYOUNG-US",
                            "box_no": "BOX-1",
                            "method_name": "海运",
                            "fba_stock_cost": "5.50",
                            "purchase_items": [{"purchase_sn": "PO260525005", "quantity": 2}],
                        }
                    ]
                },
            }
        if endpoint.endswith("productInfo"):
            return {
                "code": 0,
                "data": {
                    "sku": payload["sku"],
                    "product_name": "Product",
                    "bg_customs_export_name": "Customs Name",
                    "bg_customs_import_name": "Customs English Name",
                    "unit": "pcs",
                    "gross_weight": "1",
                    "net_weight": "0.8",
                    "outer_box_size": "10*10*10cm",
                },
            }
        if endpoint.endswith("purchaseOrderList"):
            return {"code": 0, "data": {"list": [{"order_sn": "PO260525005", "purchaser_id": 7, "supplier_name": "Supplier A"}]}}
        if endpoint.endswith("purchaser/lists"):
            return {"code": 0, "data": {"list": [{"id": 7, "name": "Purchaser A"}]}}
        if endpoint.endswith("supplier"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "supplier_name": "Supplier A",
                            "account_name": ["Supplier Shop", "义乌测试公司", "义乌测试厂"],
                            "url": "https://supplier.example.com",
                            "address_full": "Yiwu Address",
                        }
                    ]
                },
            }
        return {"code": 0, "data": {}}


class LingxingApiDataSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_warehouse = os.environ.get("LINGXING_SHIPMENT_WAREHOUSE")
        self.old_cache_dir = os.environ.get("LINGXING_CACHE_DIR")
        self.cache_tmp = tempfile.TemporaryDirectory()
        os.environ["LINGXING_SHIPMENT_WAREHOUSE"] = WAREHOUSE
        os.environ["LINGXING_CACHE_DIR"] = self.cache_tmp.name

    def tearDown(self) -> None:
        if self.old_warehouse is None:
            os.environ.pop("LINGXING_SHIPMENT_WAREHOUSE", None)
        else:
            os.environ["LINGXING_SHIPMENT_WAREHOUSE"] = self.old_warehouse
        if self.old_cache_dir is None:
            os.environ.pop("LINGXING_CACHE_DIR", None)
        else:
            os.environ["LINGXING_CACHE_DIR"] = self.old_cache_dir
        self.cache_tmp.cleanup()

    def test_filters_by_pick_time_and_warehouse(self) -> None:
        client = FakeClient()
        source = LingxingApiDataSource(client=client)

        rows = source._fetch_shipment_headers("2026-06-09")

        self.assertEqual([row["shipment_sn"] for row in rows], ["SP1"])
        self.assertEqual(client.post_payloads[0][1]["shipment_time"], "2026-06-09")
        self.assertEqual(client.post_payloads[0][1]["time_type"], 0)

    def test_shipment_list_keeps_paging_when_api_returns_less_than_requested_page_size(self) -> None:
        class ShortPageClient(FakeClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentList"):
                    page = payload.get("page")
                    if page == 1:
                        rows = [
                            {"shipment_sn": f"SP{i:03d}", "pick_time": "2026-06-09", "status": 1, "wname": WAREHOUSE}
                            for i in range(20)
                        ]
                    elif page == 2:
                        rows = [{"shipment_sn": "SP020", "pick_time": "2026-06-09", "status": 1, "wname": WAREHOUSE}]
                    else:
                        rows = []
                    return {"code": 0, "data": {"list": rows}}
                return super().post(endpoint, payload)

        client = ShortPageClient()
        rows = LingxingApiDataSource(client=client)._fetch_shipment_headers("2026-06-09")

        list_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("getInboundShipmentList")]
        self.assertEqual(len(rows), 21)
        self.assertEqual([payload["page"] for payload in list_payloads], [1, 2, 3])

    def test_shipment_list_falls_back_to_offset_when_page_repeats(self) -> None:
        class RepeatingPageClient(FakeClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentList"):
                    if "offset" in payload:
                        rows = (
                            [{"shipment_sn": "SP-OFFSET", "pick_time": "2026-06-09", "status": 1, "wname": WAREHOUSE}]
                            if payload["offset"] == 0
                            else []
                        )
                    else:
                        rows = [{"shipment_sn": "SP-REPEAT", "pick_time": "2026-06-09", "status": 1, "wname": WAREHOUSE}]
                    return {"code": 0, "data": {"list": rows}}
                return super().post(endpoint, payload)

        client = RepeatingPageClient()
        rows = LingxingApiDataSource(client=client)._fetch_shipment_headers("2026-06-09")

        self.assertEqual([row["shipment_sn"] for row in rows], ["SP-REPEAT"])
        self.assertTrue(any("offset" in payload for endpoint, payload in client.post_payloads if endpoint.endswith("getInboundShipmentList")))

    def test_detail_uses_shipment_sn_first(self) -> None:
        client = FakeClient()
        source = LingxingApiDataSource(client=client)

        source._fetch_shipment_detail({"shipment_sn": "SP260610003", "id": 66114})

        self.assertEqual(client.post_payloads[0][1], {"shipment_sn": "SP260610003"})

    def test_detail_failure_falls_back_to_header(self) -> None:
        client = FailingDetailClient()
        source = LingxingApiDataSource(client=client)
        header = {"shipment_sn": "SP260610003", "shipment_time": "2026-06-09"}

        rows = source._fetch_shipment_detail(header)

        self.assertEqual(rows, [header])

    def test_sku_failure_returns_blank_sku_info(self) -> None:
        client = FailingDetailClient()
        source = LingxingApiDataSource(client=client)

        sku_infos = source._fetch_sku_infos({"SKU-FAIL"})

        self.assertIn("SKU-FAIL", sku_infos)
        self.assertEqual(sku_infos["SKU-FAIL"].product_name, "")

    def test_sku_info_uses_cache_on_second_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cache_dir = os.environ.get("LINGXING_CACHE_DIR")
            os.environ["LINGXING_CACHE_DIR"] = tmp
            try:
                client = EnrichmentClient()
                source = LingxingApiDataSource(client=client)
                first = source._fetch_sku_infos({"00123"})
                second = source._fetch_sku_infos({"00123"})
            finally:
                if old_cache_dir is None:
                    os.environ.pop("LINGXING_CACHE_DIR", None)
                else:
                    os.environ["LINGXING_CACHE_DIR"] = old_cache_dir

        product_info_calls = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("productInfo")]
        self.assertEqual(len(product_info_calls), 1)
        self.assertEqual(first["00123"].unit, "pcs")
        self.assertEqual(second["00123"].customs_name_cn, "Customs Name")

    def test_purchase_order_cache_must_match_requested_purchase_sn(self) -> None:
        class PurchaseSnClient(EnrichmentClient):
            def post(self, endpoint, payload):
                if endpoint.endswith("purchaseOrderList"):
                    return {
                        "code": 0,
                        "data": {
                            "list": [
                                {"order_sn": "PO250917004", "purchaser_id": 113, "supplier_name": "SU00003-SY"},
                            ]
                        },
                    }
                return super().post(endpoint, payload)

        with tempfile.TemporaryDirectory() as tmp:
            old_cache_dir = os.environ.get("LINGXING_CACHE_DIR")
            os.environ["LINGXING_CACHE_DIR"] = tmp
            try:
                source = LingxingApiDataSource(client=PurchaseSnClient())
                source.cache.set("purchase_order", "PO250917004", {"order_sn": "PO-OTHER", "supplier_name": "Supplier A"})
                orders = source._fetch_purchase_orders({"PO250917004"})
            finally:
                if old_cache_dir is None:
                    os.environ.pop("LINGXING_CACHE_DIR", None)
                else:
                    os.environ["LINGXING_CACHE_DIR"] = old_cache_dir

        self.assertEqual(orders["PO250917004"]["order_sn"], "PO250917004")

    def test_refresh_cache_bypasses_sku_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cache_dir = os.environ.get("LINGXING_CACHE_DIR")
            os.environ["LINGXING_CACHE_DIR"] = tmp
            try:
                priming_client = EnrichmentClient()
                LingxingApiDataSource(client=priming_client)._fetch_sku_infos({"00123"})
                refresh_client = EnrichmentClient()
                LingxingApiDataSource(client=refresh_client, refresh_cache=True)._fetch_sku_infos({"00123"})
            finally:
                if old_cache_dir is None:
                    os.environ.pop("LINGXING_CACHE_DIR", None)
                else:
                    os.environ["LINGXING_CACHE_DIR"] = old_cache_dir

        product_info_calls = [payload for endpoint, payload in refresh_client.post_payloads if endpoint.endswith("productInfo")]
        self.assertEqual(len(product_info_calls), 1)

    def test_load_enriches_purchase_and_supplier_sources(self) -> None:
        client = EnrichmentClient()
        source = LingxingApiDataSource(client=client)

        raw = source.load("2026-06-09")

        self.assertEqual(raw.shipment_items[0].shipment_date, "2026-06-09")
        self.assertEqual(raw.shipment_items[0].seller_name, "YYOUNG-US")
        self.assertEqual(raw.shipment_items[0].box_no, "BOX-1")
        self.assertEqual(raw.shipment_items[0].transport_method, "Rail")
        self.assertEqual(raw.sku_infos["00123"].customs_name_cn, "Customs Name")
        self.assertEqual(raw.sku_infos["00123"].customs_name_en, "Customs English Name")
        self.assertEqual(raw.purchase_batches[0].purchase_sn, "PO260525005")
        self.assertEqual(raw.purchase_batches[0].quantity, Decimal("2"))
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Purchaser A")
        self.assertEqual(raw.purchase_batches[0].supplier, "义乌测试公司")
        self.assertEqual(raw.purchase_batches[0].domestic_source, "https://supplier.example.com")
        purchase_order_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("purchaseOrderList")]
        self.assertEqual(
            purchase_order_payloads,
            [{"start_date": "2026-05-25", "end_date": "2026-05-25", "search_field_time": "create_time", "purchase_sn": "PO260525005"}],
        )

    def test_supplier_infos_are_fetched_realtime_and_ignore_cache(self) -> None:
        source = LingxingApiDataSource(client=EnrichmentClient())
        source.cache.set("supplier_infos", "all", {"Supplier A": {"account_name": "Old Supplier", "url": "https://old.example.com"}})

        supplier_infos = source._fetch_supplier_infos()

        self.assertEqual(supplier_infos["Supplier A"]["url"], "https://supplier.example.com")
        self.assertEqual(supplier_infos["Supplier A"]["account_name"], "\u4e49\u4e4c\u6d4b\u8bd5\u516c\u53f8")
        cached = source.cache.get("supplier_infos", "all", ttl_days=1)
        self.assertEqual(cached["Supplier A"]["url"], "https://old.example.com")

    def test_supplier_url_is_used_as_domestic_source_for_zhuji_supplier(self) -> None:
        class ZhujiSupplierClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentList"):
                    rows = [{"shipment_sn": "SP260623003", "pick_time": "2026-06-23", "status": 1, "wname": WAREHOUSE}] if payload.get("page", 1) == 1 else []
                    return {"code": 0, "data": {"list": rows}}
                if endpoint.endswith("purchaseOrderList"):
                    return {"code": 0, "data": {"list": [{"order_sn": "PO260525005", "purchaser_id": 7, "supplier_name": "SU00004-YZ"}]}}
                if endpoint.endswith("supplier"):
                    return {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "supplier_name": "SU00004-YZ",
                                    "account_name": "\u8bf8\u66a8\u5e02\u5955\u81fb\u9488\u7ec7\u6709\u9650\u516c\u53f8",
                                    "url": "\u6d59\u6c5f\u8bf8\u66a8",
                                }
                            ]
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=ZhujiSupplierClient()).load("2026-06-23")

        self.assertEqual(raw.purchase_batches[0].shipment_no, "SP260623003")
        self.assertEqual(raw.purchase_batches[0].supplier, "\u8bf8\u66a8\u5e02\u5955\u81fb\u9488\u7ec7\u6709\u9650\u516c\u53f8")
        self.assertEqual(raw.purchase_batches[0].domestic_source, "\u6d59\u6c5f\u8bf8\u66a8")

    def test_transport_method_falls_back_to_normalized_method_name_when_track_list_empty(self) -> None:
        class EmptyTrackListClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "head_logistics_list": {"track_list": []},
                            "items": [
                                {
                                    "sku": "00123",
                                    "quantity": 2,
                                    "box_no": "BOX-1",
                                    "method_name": "海卡",
                                    "fba_stock_cost": "5.50",
                                    "purchase_items": [{"purchase_sn": "PO260525005", "quantity": 2}],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=EmptyTrackListClient()).load("2026-06-09")

        self.assertEqual(raw.shipment_items[0].transport_method, "海运")

    def test_purchase_order_uses_only_purchase_sn_with_parsed_date(self) -> None:
        class PurchaseOrderClient(FakeClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("purchaseOrderList"):
                    return {
                        "code": 0,
                        "data": {"list": [{"order_sn": payload["purchase_sn"], "purchaser_id": 113, "supplier_name": "Supplier A"}]},
                    }
                return super().post(endpoint, payload)

        client = PurchaseOrderClient()
        source = LingxingApiDataSource(client=client)

        orders = source._fetch_purchase_orders({"PO260525005"})

        purchase_order_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("purchaseOrderList")]
        self.assertEqual(len(purchase_order_payloads), 1)
        self.assertEqual(
            purchase_order_payloads[0],
            {"start_date": "2026-05-25", "end_date": "2026-05-25", "search_field_time": "create_time", "purchase_sn": "PO260525005"},
        )
        self.assertEqual(orders["PO260525005"]["order_sn"], "PO260525005")

    def test_purchase_order_with_unparseable_date_does_not_call_api(self) -> None:
        client = FakeClient()
        source = LingxingApiDataSource(client=client)

        orders = source._fetch_purchase_orders({"PO-1"})

        purchase_order_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("purchaseOrderList")]
        self.assertEqual(orders, {})
        self.assertEqual(purchase_order_payloads, [])

    def test_purchase_order_whitelist_error_stops_parameter_attempts(self) -> None:
        class WhitelistPurchaseOrderClient(FakeClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("purchaseOrderList"):
                    raise LingxingClientError("ip not permit, please add ip to white list first.")
                return super().post(endpoint, payload)

        client = WhitelistPurchaseOrderClient()
        source = LingxingApiDataSource(client=client)

        orders = source._fetch_purchase_orders({"PO260101001", "PO260101002"})

        purchase_order_payloads = [payload for endpoint, payload in client.post_payloads if endpoint.endswith("purchaseOrderList")]
        self.assertEqual(orders, {})
        self.assertEqual(len(purchase_order_payloads), 1)

    def test_detail_child_items_keep_parent_box_and_purchase_items(self) -> None:
        class NestedDetailClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "box_no": "BOX-PARENT",
                                    "purchase_items": [{"purchase_sn": "PO260525005", "quantity": 3}],
                                    "products": [{"sku": "00123", "quantity": 3, "fba_stock_cost": "5.50", "shipment_time": "1780985590"}],
                                }
                            ]
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=NestedDetailClient()).load("2026-06-09")

        self.assertEqual(raw.shipment_items[0].box_no, "BOX-PARENT")
        self.assertEqual(raw.shipment_items[0].shipment_date, "2026-06-09")
        self.assertEqual(raw.purchase_batches[0].purchase_sn, "PO260525005")
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Purchaser A")

    def test_detail_uses_outbound_batch_when_purchase_items_empty(self) -> None:
        class OutboundBatchClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [{"sku": "00123", "quantity_shipped": 14, "fba_stock_cost": "5.50", "purchase_items": []}],
                            "outbound_batch": [
                                {
                                    "sku": "00123",
                                    "batch_record_list": [
                                        {
                                            "warehouse_batch_id": "2606090056-1",
                                            "outbound_num": 14,
                                            "purchase_order_sns": ["PO260525005"],
                                            "supplier_names": ["Supplier A"],
                                            "unit_purchase_price": "$ 5.5000",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=OutboundBatchClient()).load("2026-06-09")

        self.assertEqual(raw.purchase_batches[0].purchase_sn, "PO260525005")
        self.assertEqual(raw.purchase_batches[0].batch_no, "2606090056-1")
        self.assertEqual(raw.purchase_batches[0].quantity, Decimal("14"))
        self.assertEqual(raw.purchase_batches[0].purchase_unit_price, Decimal("5.5000"))
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Purchaser A")

    def test_sp260609037_style_detail_fields_are_mapped(self) -> None:
        class Sp260609037Client(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "sku": "00123",
                                    "product_name": "Detail Product",
                                    "quantity_shipped": 28,
                                    "num": 28,
                                    "fba_stock_cost": "9.2500",
                                    "total_gw": "3.39",
                                    "cg_box_length": "55.00",
                                    "cg_box_width": "43.01",
                                    "cg_box_height": "30.99",
                                    "purchase_items": [],
                                }
                            ],
                            "auxs": [{"gmt_modified": "2026-06-10 12:00:00"}],
                            "outbound_batch": [
                                {
                                    "sku": "00123",
                                    "batch_record_list": [
                                        {
                                            "warehouse_batch_id": "2606090056-1",
                                            "outbound_num": 28,
                                            "purchase_order_sns": ["PO-1"],
                                            "supplier_names": ["Supplier A"],
                                            "unit_purchase_price": "$ 9.2500",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=Sp260609037Client()).load("2026-06-09")

        item = raw.shipment_items[0]
        self.assertEqual(item.quantity, Decimal("28"))
        self.assertEqual(item.product_name, "Detail Product")
        self.assertEqual(item.purchase_unit_price, Decimal("9.2500"))
        self.assertEqual(item.total_gross_weight, Decimal("3.39"))
        self.assertEqual(item.outer_box_size, "55.00*43.01*30.99")
        self.assertEqual(item.volume, Decimal("0.0733083945"))
        self.assertEqual(item.updated_at, "2026-06-10 12:00:00")
        self.assertEqual(raw.purchase_batches[0].quantity, Decimal("28"))

    def test_detail_uses_item_box_no_for_box_no(self) -> None:
        class BoxListClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "sku": "00123",
                                    "quantity_shipped": 14,
                                    "box_no": "ITEM-BOX-1",
                                    "sku_box_key": "SKU-BOX-1",
                                    "cg_box_length": "0.00",
                                    "cg_box_width": "0.00",
                                    "cg_box_height": "0.00",
                                    "fba_stock_cost": "5.50",
                                    "purchase_items": [{"purchase_sn": "PO-1", "quantity": 14}],
                                }
                            ],
                            "box_list": [
                                {
                                    "box_num": 1,
                                    "cg_box_weight": "21.41",
                                    "cg_box_length": "55.00",
                                    "cg_box_width": "43.01",
                                    "cg_box_height": "30.99",
                                    "box_range": "1",
                                    "box_codes": "FBA19FSM0JH4U000001",
                                    "box_skus": [{"sku_box_key": "SKU-BOX-1", "sku": "00123", "quantity_in_case": 14}],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=BoxListClient()).load("2026-06-09")

        self.assertEqual(raw.shipment_items[0].box_no, "ITEM-BOX-1")
        self.assertEqual(raw.shipment_items[0].box_count, Decimal("1"))
        self.assertEqual(raw.shipment_items[0].total_gross_weight, Decimal("21.41"))
        self.assertEqual(raw.shipment_items[0].outer_box_size, "55.00*43.01*30.99")
        self.assertEqual(raw.shipment_items[0].volume, Decimal("0.0733083945"))
        self.assertEqual(raw.purchase_batches[0].box_no, "ITEM-BOX-1")

    def test_detail_falls_back_to_box_list_when_item_box_no_is_blank(self) -> None:
        class BlankItemBoxClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "sku": "00123",
                                    "quantity_shipped": 42,
                                    "box_no": "",
                                    "sku_box_key": "SKU-BOX-1",
                                    "fba_stock_cost": "5.50",
                                    "purchase_items": [{"purchase_sn": "PO250917004", "quantity": 42}],
                                }
                            ],
                            "box_list": [
                                {
                                    "box_num": 3,
                                    "box_codes": "FBA19G3DRWRYU000001\nFBA19G3DRWRYU000002\nFBA19G3DRWRYU000003",
                                    "box_skus": [{"sku_box_key": "SKU-BOX-1", "sku": "00123", "quantity_in_case": 14}],
                                }
                            ],
                        },
                    }
                if endpoint.endswith("purchaseOrderList"):
                    return {
                        "code": 0,
                        "data": {
                            "list": [
                                {"order_sn": "PO250917004", "purchaser_id": 7, "supplier_name": "Supplier A"},
                            ]
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=BlankItemBoxClient()).load("2026-06-09")

        self.assertEqual(raw.shipment_items[0].box_no, "FBA19G3DRWRYU000001\nFBA19G3DRWRYU000002\nFBA19G3DRWRYU000003")
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Purchaser A")

    def test_detail_expands_one_sku_across_multiple_boxes(self) -> None:
        class MultiBoxClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    box_list = []
                    for index in range(1, 7):
                        box_list.append(
                            {
                                "box_num": 1,
                                "cg_box_weight": "19.70",
                                "cg_box_length": "30.99",
                                "cg_box_width": "55.00",
                                "cg_box_height": "43.01",
                                "box_codes": f"FBA19FSGHQ6KU{index:06d}",
                                "box_skus": [
                                    {
                                        "sku_box_key": "SKU-BOX-1",
                                        "sku": "221020152704",
                                        "shipment_id": "FBA19FSGHQ6K",
                                        "msku": "1-3F-H-D",
                                        "quantity_in_case": 158,
                                    }
                                ],
                            }
                        )
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "sku": "221020152704",
                                    "quantity_shipped": 948,
                                    "num": 948,
                                    "sku_box_key": "SKU-BOX-1",
                                    "shipment_id": "FBA19FSGHQ6K",
                                    "msku": "1-3F-H-D",
                                    "fba_stock_cost": "8.7500",
                                    "box_no": "ITEM-BOX-RANGE",
                                    "purchase_items": [],
                                }
                            ],
                            "box_list": box_list,
                            "outbound_batch": [
                                {
                                    "sku": "221020152704",
                                    "batch_record_list": [
                                        {
                                            "outbound_num": 948,
                                            "purchase_order_sns": ["PO-1"],
                                            "supplier_names": ["Supplier A"],
                                            "unit_purchase_price": "$ 8.7500",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=MultiBoxClient()).load("2026-06-09")

        self.assertEqual(len(raw.shipment_items), 6)
        self.assertEqual([item.box_no for item in raw.shipment_items], ["ITEM-BOX-RANGE"] * 6)
        self.assertTrue(all(item.quantity == Decimal("158") for item in raw.shipment_items))
        self.assertEqual(len(raw.purchase_batches), 6)
        self.assertTrue(all(batch.quantity == Decimal("158") for batch in raw.purchase_batches))

    def test_detail_expands_packed_box_only_for_matching_sku_box_key(self) -> None:
        class PackedBoxClient(EnrichmentClient):
            def post(self, endpoint, payload):
                self.post_payloads.append((endpoint, payload))
                if endpoint.endswith("getInboundShipmentListMwsDetail"):
                    return {
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "sku": "240520456756",
                                    "product_name": "4pcs leggings L",
                                    "quantity_shipped": 45,
                                    "num": 45,
                                    "sku_box_key": "SKU-L",
                                    "shipment_id": "FBA19FSF3TP2",
                                    "msku": "4-5FKD-HCHZI-L",
                                    "fba_stock_cost": "40.0000",
                                    "box_no": "ITEM-BOX-PACKED",
                                    "purchase_items": [],
                                }
                            ],
                            "box_list": [
                                {
                                    "box_num": 1,
                                    "cg_box_weight": "19.70",
                                    "cg_box_length": "52.00",
                                    "cg_box_width": "42.02",
                                    "cg_box_height": "32.01",
                                    "box_codes": "FBA19FSF3TP2U000013",
                                    "box_skus": [
                                        {
                                            "sku_box_key": "SKU-2XL",
                                            "sku": "240520466758",
                                            "shipment_id": "FBA19FSF3TP2",
                                            "msku": "4-5FKD-HCHZI-2XL",
                                            "quantity_in_case": 17,
                                        },
                                        {
                                            "sku_box_key": "SKU-L",
                                            "sku": "240520456756",
                                            "shipment_id": "FBA19FSF3TP2",
                                            "msku": "4-5FKD-HCHZI-L",
                                            "quantity_in_case": 13,
                                        },
                                    ],
                                },
                                {
                                    "box_num": 1,
                                    "cg_box_weight": "19.70",
                                    "cg_box_length": "52.00",
                                    "cg_box_width": "42.02",
                                    "cg_box_height": "32.01",
                                    "box_codes": "FBA19FSF3TP2U000014",
                                    "box_skus": [
                                        {
                                            "sku_box_key": "SKU-L",
                                            "sku": "240520456756",
                                            "shipment_id": "FBA19FSF3TP2",
                                            "msku": "4-5FKD-HCHZI-L",
                                            "quantity_in_case": 32,
                                        }
                                    ],
                                },
                            ],
                            "outbound_batch": [
                                {
                                    "sku": "240520456756",
                                    "batch_record_list": [
                                        {
                                            "outbound_num": 45,
                                            "purchase_order_sns": ["PO-1"],
                                            "supplier_names": ["Supplier A"],
                                            "unit_purchase_price": "$ 40.0000",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=PackedBoxClient()).load("2026-06-09")

        self.assertEqual([item.box_no for item in raw.shipment_items], ["ITEM-BOX-PACKED", "ITEM-BOX-PACKED"])
        self.assertEqual([item.quantity for item in raw.shipment_items], [Decimal("13"), Decimal("32")])
        self.assertEqual([batch.quantity for batch in raw.purchase_batches], [Decimal("13"), Decimal("32")])

    def test_purchaser_env_map_fills_entity_when_purchaser_api_unavailable(self) -> None:
        class NoPurchaserPermissionClient(EnrichmentClient):
            def post(self, endpoint, payload):
                if endpoint.endswith("purchaser/lists"):
                    return {"code": "403", "msg": "permission denied", "data": None}
                return super().post(endpoint, payload)

        old = os.environ.get("LINGXING_PURCHASER_ID_NAME_MAP")
        os.environ["LINGXING_PURCHASER_ID_NAME_MAP"] = "7:Local Purchaser"
        try:
            raw = LingxingApiDataSource(client=NoPurchaserPermissionClient()).load("2026-06-09")
        finally:
            if old is None:
                os.environ.pop("LINGXING_PURCHASER_ID_NAME_MAP", None)
            else:
                os.environ["LINGXING_PURCHASER_ID_NAME_MAP"] = old

        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Local Purchaser")

    def test_purchase_order_matches_real_order_sn_field(self) -> None:
        class OrderSnClient(EnrichmentClient):
            def post(self, endpoint, payload):
                if endpoint.endswith("purchaseOrderList"):
                    return {
                        "code": 0,
                        "data": {
                            "list": [
                                {"order_sn": "PO-OTHER", "purchaser_id": 1, "supplier_name": "Supplier B"},
                                {"order_sn": "PO260525005", "purchaser_id": 7, "supplier_name": "Supplier A"},
                            ]
                        },
                    }
                return super().post(endpoint, payload)

        raw = LingxingApiDataSource(client=OrderSnClient()).load("2026-06-09")

        self.assertEqual(raw.purchase_batches[0].purchase_entity, "Purchaser A")
        self.assertEqual(raw.purchase_batches[0].supplier, "义乌测试公司")


if __name__ == "__main__":
    unittest.main()
