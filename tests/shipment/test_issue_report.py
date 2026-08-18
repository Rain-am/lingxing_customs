from __future__ import annotations

import unittest
from decimal import Decimal

from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.issue_report import build_shipment_issue_payloads, build_shipment_replacement_scopes
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


if __name__ == "__main__":
    unittest.main()
