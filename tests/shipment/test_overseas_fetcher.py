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
        if endpoint.endswith("/owms/inbound/listInbound"):
            return {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "overseas_order_no": "OW260703001",
                            "update_time": 1783094400000,
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
                    "logistics_name": "Overseas Carrier",
                    "logistics_way_name": "Fast Channel",
                    "logisticsInfo": {
                        "head_logistics_tracking_info": {"transport_type_name": "海运"},
                    },
                    "total": {"package_num": 24},
                    "products": [
                        {
                            "sku": "SKU-OW-1",
                            "product_name": "Overseas Product",
                            "awd_shipment_id": "AWD-1",
                            "seller_arr": [{"seller_name": "Shop A"}],
                            "batch_record_list": [
                                {
                                    "supplier_names": ["Supplier A"],
                                    "unit_storage_cost": "3.25",
                                }
                            ],
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
                                    "total_box_weight": "11.50",
                                    "total_box_volume": "0.123456",
                                },
                                "box_list": [
                                    {"box_no": "BOX-1", "length": "10", "width": "20", "height": "30"},
                                    {"box_no": "BOX-2", "length": "10", "width": "20", "height": "30"},
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
        self.assertEqual(item.logistics_provider, "Overseas Carrier")
        self.assertEqual(item.logistics_channel, "Fast Channel")
        self.assertEqual(item.transport_method, "海运")
        self.assertEqual(item.logistics_center_code, "AWD-CENTER-1")
        self.assertEqual(item.box_no, "BOX-1\nBOX-2")
        self.assertEqual(item.box_count, Decimal("2"))
        self.assertEqual(item.total_gross_weight, Decimal("11.50"))
        self.assertEqual(item.outer_box_size, "10*20*30")
        self.assertEqual(item.volume, Decimal("0.123456"))
        self.assertEqual(item.updated_at, "2026-07-04 00:00:00")

        batch = raw.purchase_batches[0]
        self.assertEqual(batch.supplier, "Supplier A Company")
        self.assertEqual(batch.domestic_source, "浙江义乌")
        self.assertEqual(batch.purchase_unit_price, Decimal("3.25"))

    def test_overseas_rows_can_build_customs_rows(self) -> None:
        raw = OverseasWarehouseApiDataSource(client=OverseasClient()).load("2026-07-03")

        workbook = build_customs_workbook_data(raw)

        row = workbook.customs_rows[0]
        self.assertEqual(row.shipment_date, "2026-07")
        self.assertEqual(row.shipment_day, "2026-07-03")
        self.assertEqual(row.shipment_no, "OW260703001")
        self.assertEqual(row.seller_name, "Shop A")
        self.assertEqual(row.box_no, "BOX-1,BOX-2")
        self.assertEqual(row.box_count, Decimal("2"))
        self.assertEqual(row.total_net_weight, Decimal("9.50"))


if __name__ == "__main__":
    unittest.main()
