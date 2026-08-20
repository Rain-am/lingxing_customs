from __future__ import annotations

import unittest
from unittest.mock import patch

import src.product.issue_report as issue_report
from src.product.issue_report import build_product_issue_payloads, load_active_product_issue_payloads
from src.shipment.export_mysql import MySQLConfig


class ProductIssueReportTest(unittest.TestCase):
    def test_builds_one_issue_per_product_with_missing_required_fields(self) -> None:
        payloads = build_product_issue_payloads(
            [
                {
                    "sku": "SKU-1",
                    "name": "测试产品",
                    "chinese_customs_name": "",
                    "unit": "个",
                    "customs_code": None,
                    "report_element": "塑料",
                    "update_time": "2026-08-17 09:00:00",
                }
            ]
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["field_name"], "产品资料")
        self.assertEqual(payloads[0]["issue"], "产品资料缺失：中文报关品名、海关编码")
        self.assertEqual(payloads[0]["raw_json"]["missing_fields"], ["中文报关品名", "海关编码"])
        self.assertEqual(payloads[0]["raw_json"]["missing_columns"], ["chinese_customs_name", "customs_code"])
        self.assertEqual(payloads[0]["scope"], "product")
        self.assertEqual(payloads[0]["source"], "product_sync")
        self.assertEqual(payloads[0]["category"], "product_master")
        self.assertEqual(payloads[0]["sku"], "SKU-1")
        self.assertEqual(payloads[0]["product_name"], "测试产品")
        self.assertTrue(payloads[0]["issue_key"].startswith("product:"))

    def test_loads_active_products_from_mysql_before_building_issues(self) -> None:
        connection = FakeConnection(
            [
                ("SKU-1", "产品A", "塑料", "个", "", "123", "2026-08-17 09:00:00", 1),
                ("SKU-2", "产品B", "塑料", "个", "塑料杯", "456", "2026-08-17 09:00:00", 1),
            ]
        )

        with patch.object(issue_report, "_open_mysql_connection", return_value=(connection, None)):
            payloads = load_active_product_issue_payloads(mysql_config(), table="customs_product")

        self.assertIn("WHERE `is_enabled` = 1", connection.cursor_obj.execute_calls[0])
        self.assertEqual([item["sku"] for item in payloads], ["SKU-1"])
        self.assertEqual([item["field_name"] for item in payloads], ["产品资料"])
        self.assertEqual([item["raw_json"]["missing_fields"] for item in payloads], [["中文报关品名"]])
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

    def execute(self, sql):
        self.execute_calls.append(sql)

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
