from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

import src.shipment.issue_report as issue_report
from src.shipment.export_mysql import MySQLConfig
from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.issue_report import (
    build_shipment_issue_payloads,
    build_shipment_replacement_scopes,
    build_split_supplier_issue_payloads,
    load_split_supplier_issue_payloads,
)
from src.shipment.models import RawCustomsData, ShipmentItem, SkuInfo


class ShipmentIssueReportTest(unittest.TestCase):
    def test_builds_payloads_with_category_and_context(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-08-17 10:00:00",
                    shipment_no="SP260817001",
                    sku="SKU-1",
                    quantity=Decimal("2"),
                    product_name="测试产品",
                    box_no="BOX-1",
                    seller_name="店铺A",
                    dest_country="US",
                    source="amazon",
                )
            ],
            sku_infos={
                "SKU-1": SkuInfo(
                    sku="SKU-1",
                    product_name="测试产品",
                    customs_name_cn="",
                    unit="个",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10",
                )
            },
        )
        workbook_data = build_customs_workbook_data(raw)

        payloads = build_shipment_issue_payloads(
            raw,
            workbook_data,
            shipment_times=["2026-08-17"],
            source_names=["amazon"],
        )

        customs_name_issue = next(item for item in payloads if item["field_name"] == "中文报关品名")
        self.assertEqual(customs_name_issue["scope"], "shipment")
        self.assertEqual(customs_name_issue["category"], "product_master")
        self.assertEqual(customs_name_issue["severity"], "high")
        self.assertEqual(customs_name_issue["source"], "amazon")
        self.assertEqual(customs_name_issue["shipment_day"], "2026-08-17")
        self.assertEqual(customs_name_issue["shipment_no"], "SP260817001")
        self.assertEqual(customs_name_issue["box_no"], "BOX-1")
        self.assertEqual(customs_name_issue["sku"], "SKU-1")
        self.assertEqual(customs_name_issue["product_name"], "测试产品")
        self.assertTrue(customs_name_issue["issue_key"].startswith("shipment:"))
        self.assertEqual(customs_name_issue["raw_json"]["seller_name"], "店铺A")

    def test_replacement_scopes_cover_requested_and_observed_sources(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-08-17",
                    shipment_no="OWS260817001",
                    sku="SKU-1",
                    quantity=Decimal("1"),
                    source="overseas",
                )
            ]
        )

        scopes = build_shipment_replacement_scopes(
            raw,
            shipment_times=["2026-08-17"],
            source_names=["amazon"],
        )

        self.assertEqual(
            scopes,
            [
                {"scope": "shipment", "source": "amazon", "shipment_day": "2026-08-17"},
                {"scope": "shipment", "source": "overseas", "shipment_day": "2026-08-17"},
            ],
        )

    def test_builds_payload_for_split_supplier_rows_from_mysql(self) -> None:
        payloads = build_split_supplier_issue_payloads(
            [
                {
                    "id": "row1",
                    "confirm_shipment": "2026-08-24",
                    "tran_id": "SP260824001",
                    "seller_name": "POPRED-US",
                    "final_cust": "FINAL",
                    "item_code": "SKU-1",
                    "name": "Product",
                    "box_no": "BOX-1",
                    "supplier_name": "供应商A / 供应商B",
                    "supplier_addr": "浙江诸暨",
                },
                {
                    "id": "row2",
                    "confirm_shipment": "2026-08-24",
                    "tran_id": "SP260824002",
                    "item_code": "SKU-2",
                    "supplier_name": "供应商A",
                },
            ],
            source_names=["amazon"],
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["scope"], "shipment")
        self.assertEqual(payloads[0]["category"], "purchase_supply")
        self.assertEqual(payloads[0]["source"], "amazon")
        self.assertEqual(payloads[0]["shipment_day"], "2026-08-24")
        self.assertEqual(payloads[0]["shipment_no"], "SP260824001")
        self.assertEqual(payloads[0]["box_no"], "BOX-1")
        self.assertEqual(payloads[0]["sku"], "SKU-1")
        self.assertEqual(payloads[0]["field_name"], "供应商")
        self.assertEqual(payloads[0]["issue"], "供应商名称存在多个候选，请确认采购单供应商")
        self.assertEqual(payloads[0]["raw_json"]["supplier_name"], "供应商A / 供应商B")
        self.assertTrue(payloads[0]["issue_key"].startswith("shipment:"))

    def test_loads_split_supplier_rows_from_mysql_for_requested_days_and_sources(self) -> None:
        connection = FakeConnection(
            [
                (
                    "row1",
                    "2026-08-24",
                    "SP260824001",
                    "POPRED-US",
                    "FINAL",
                    "SKU-1",
                    "Product",
                    "BOX-1",
                    "供应商A / 供应商B",
                    "浙江诸暨",
                )
            ]
        )

        with patch.object(issue_report, "_open_mysql_connection", return_value=(connection, None)):
            payloads = load_split_supplier_issue_payloads(
                mysql_config(),
                table="customs_bill_parcels",
                shipment_times=["2026-08-24 10:00:00"],
                source_names=["amazon"],
            )

        self.assertEqual(len(payloads), 1)
        self.assertIn("`supplier_name` LIKE %s", connection.cursor_obj.execute_calls[0][0])
        self.assertIn("`confirm_shipment` IN (%s)", connection.cursor_obj.execute_calls[0][0])
        self.assertIn("`tran_id` LIKE %s", connection.cursor_obj.execute_calls[0][0])
        self.assertEqual(connection.cursor_obj.execute_calls[0][1], ["%/%", "2026-08-24", "SP%"])
        self.assertTrue(connection.closed)


def mysql_config() -> MySQLConfig:
    return MySQLConfig(
        host="127.0.0.1",
        port=3306,
        user="user",
        password="password",
        database="db",
    )


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


if __name__ == "__main__":
    unittest.main()
