from __future__ import annotations

import unittest
from decimal import Decimal

from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.overseas_fetcher import OverseasWarehouseApiDataSource


class OverseasClient:
    def __init__(self) -> None:
        self.post_payloads = []
        self.config = type("Config", (), {"page_size": 100})()

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        if endpoint.endswith("/local_inventory/supplier"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "supplier_name": "Supplier A",
                            "account_name": "Supplier A Company",
                            "url": "浙江义乌",
                        }
                    ]
                },
            }
        if endpoint.endswith("/local_inventory/purchaseOrderList"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "purchase_sn": "PO260701001",
                            "supplier_name": "Supplier A",
                        }
                    ]
                },
            }
        if endpoint.endswith("/local_inventory/productInfo"):
            return {
                "code": 0,
                "data": {
                    "sku": payload["sku"],
                    "unit": "pcs",
                    "bg_customs_export_name": "Customs CN",
                },
            }
        if endpoint.endswith("/owms/inbound/listInbound"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "overseas_order_no": "OW260703001",
                            "update_time": 1783094400000,
                            "head_logistics_list": [
                                {
                                    "tracking_list": [
                                        {"transport_type": 1},
                                        {"transport_type": 2},
                                    ]
                                }
                            ],
                            "products": [
                                {
                                    "sku": "SKU-OW-1",
                                    "batch_record_list": [
                                        {
                                            "purchase_order_sns": ["PO260701001"],
                                            "supplier_names": ["Fallback Supplier"],
                                            "unit_storage_cost": "3.25",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        if endpoint.endswith("/overSeaWarehouse/stockOrder/detail"):
            return {
                "code": 0,
                "data": {
                    "real_delivery_time": "2026-07-03 09:30:00",
                    "overseas_order_no": "OW260703001",
                    "logistics_name": "Carrier (Overseas Carrier Company)",
                    "logistics_way_name": "Fast Channel",
                    "logisticsInfo": {
                        "head_logistics_tracking_info": [
                            {"transport_type_name": "快递"},
                            {"transport_type_name": "海运"},
                        ],
                    },
                    "total": {"package_num": 24},
                    "products": [
                        {
                            "sku": "SKU-OW-1",
                            "product_name": "Overseas Product",
                            "awd_shipment_id": "AWD-1",
                            "seller_arr": [{"seller_name": "Shop A"}],
                        }
                    ],
                },
            }
        if endpoint.endswith("/awd/inbound-plan/detail"):
            return {
                "code": 0,
                "data": {
                    "awdShipmentVOS": [
                        {"shipmentId": "AWD-1", "warehouseReferenceId": "AWD-CENTER-1"},
                    ]
                },
            }
        if endpoint.endswith("/owms/inbound/getPackingData"):
            return {
                "code": 0,
                "data": {
                    "box_count": 2,
                    "box_data": {
                        "box_content": [
                            {
                                "boxInfo": {
                                    "total_box_weight": "99.99",
                                    "total_box_volume": "9.999999",
                                },
                                "box_list": [
                                    {"box_no": "1-56", "length": "99", "width": "99", "height": "99"},
                                ],
                            },
                            {
                                "sku": "SKU-OW-1",
                                "boxInfo": {
                                    "total_box_weight": "11.50",
                                    "total_box_volume": "0.123456",
                                },
                                "box_list": [
                                    {"box_no": "BOX-35", "length": "10", "width": "20", "height": "30"},
                                    {"box_no": "BOX-36", "length": "10", "width": "20", "height": "30"},
                                ],
                            }
                        ]
                    },
                },
            }
        raise AssertionError(endpoint)


class OverseasWarehouseApiDataSourceTest(unittest.TestCase):
    def test_load_maps_overseas_order_fields_to_raw_customs_data(self) -> None:
        source = OverseasWarehouseApiDataSource(client=OverseasClient())

        raw = source.load("2026-07-03")

        item = raw.shipment_items[0]
        self.assertEqual(item.shipment_date, "2026-07-03")
        self.assertEqual(item.shipment_no, "OW260703001")
        self.assertEqual(item.seller_name, "Shop A")
        self.assertEqual(item.sku, "SKU-OW-1")
        self.assertEqual(item.product_name, "Overseas Product")
        self.assertEqual(item.quantity, Decimal("24"))
        self.assertEqual(item.purchase_unit_price, Decimal("3.25"))
        self.assertEqual(item.supplier, "Supplier A Company")
        self.assertEqual(item.domestic_source, "浙江义乌")
        self.assertEqual(item.logistics_provider, "Overseas Carrier Company")
        self.assertEqual(item.logistics_channel, "Fast Channel")
        self.assertEqual(item.transport_method, "海运")
        self.assertEqual(item.logistics_center_code, "AWD-CENTER-1")
        self.assertEqual(item.box_no, "35-36")
        self.assertEqual(item.box_count, "")
        self.assertEqual(item.total_gross_weight, Decimal("11.50"))
        self.assertEqual(item.outer_box_size, "10*20*30")
        self.assertEqual(item.volume, Decimal("0.123456"))
        self.assertEqual(item.updated_at, "2026-07-04 00:00:00")

        batch = raw.purchase_batches[0]
        self.assertEqual(batch.supplier, "Supplier A Company")
        self.assertEqual(batch.domestic_source, "浙江义乌")
        self.assertEqual(batch.purchase_sn, "PO260701001")
        self.assertEqual(batch.purchase_unit_price, Decimal("3.25"))

    def test_overseas_rows_can_build_customs_rows(self) -> None:
        raw = OverseasWarehouseApiDataSource(client=OverseasClient()).load("2026-07-03")

        workbook = build_customs_workbook_data(raw)

        row = workbook.customs_rows[0]
        self.assertEqual(row.shipment_date, "2026-07")
        self.assertEqual(row.shipment_day, "2026-07-03")
        self.assertEqual(row.shipment_no, "OW260703001")
        self.assertEqual(row.seller_name, "Shop A")
        self.assertEqual(row.box_no, "35-36")
        self.assertEqual(row.box_count, "")
        self.assertEqual(row.total_net_weight, Decimal("11.50"))
        self.assertEqual(row.customs_name_cn, "Customs CN")
        self.assertEqual(row.unit, "pcs")

    def test_overseas_duplicate_box_metrics_are_not_zeroed(self) -> None:
        class MultiSkuClient(OverseasClient):
            def post(self, endpoint, payload):
                data = super().post(endpoint, payload)
                if endpoint.endswith("/overSeaWarehouse/stockOrder/detail"):
                    data["data"]["products"].append(
                        {
                            "sku": "SKU-OW-2",
                            "product_name": "Overseas Product 2",
                            "awd_shipment_id": "AWD-1",
                            "seller_arr": [{"seller_name": "Shop A"}],
                            "batch_record_list": [
                                {
                                    "purchase_order_sns": ["PO260701001"],
                                    "supplier_names": ["Fallback Supplier"],
                                    "unit_storage_cost": "4.25",
                                }
                            ],
                        }
                    )
                if endpoint.endswith("/owms/inbound/getPackingData"):
                    data["data"]["box_data"]["box_content"].append(
                        {
                            "sku": "SKU-OW-2",
                            "boxInfo": {
                                "total_box_weight": "11.50",
                                "total_box_volume": "0.123456",
                            },
                            "box_list": [
                                {"box_no": "BOX-35", "length": "10", "width": "20", "height": "30"},
                                {"box_no": "BOX-36", "length": "10", "width": "20", "height": "30"},
                            ],
                        }
                    )
                return data

        raw = OverseasWarehouseApiDataSource(client=MultiSkuClient()).load("2026-07-03")

        workbook = build_customs_workbook_data(raw)

        self.assertEqual(len(workbook.customs_rows), 2)
        self.assertTrue(all(row.total_gross_weight == Decimal("11.50") for row in workbook.customs_rows))
        self.assertTrue(all(row.outer_box_size == "10*20*30" for row in workbook.customs_rows))
        self.assertTrue(all(row.volume == Decimal("0.123456") for row in workbook.customs_rows))


if __name__ == "__main__":
    unittest.main()
