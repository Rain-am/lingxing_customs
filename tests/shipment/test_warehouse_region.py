from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.common.xlsx_writer import write_xlsx_workbook
from src.shipment.models import RawCustomsData, ShipmentItem
from src.shipment.warehouse_region import apply_warehouse_region_mapping, load_warehouse_region_mapping, region_for_center


class WarehouseRegionTest(unittest.TestCase):
    def test_loads_excel_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "warehouse-region.xlsx"
            _write_mapping(path, [("美东", "ABE8"), ("美西", "ONT8")])

            mapping = load_warehouse_region_mapping(path)

        self.assertEqual(mapping["ABE8"], "美东")
        self.assertEqual(region_for_center(" abe8 ", mapping), "美东")
        self.assertEqual(region_for_center("MISSING", mapping), "")

    def test_applies_mapping_to_amazon_items_only(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-07-06",
                    shipment_no="SP1",
                    sku="SKU1",
                    quantity=Decimal("1"),
                    logistics_center_code=" abe8 ",
                ),
                ShipmentItem(
                    shipment_date="2026-07-06",
                    shipment_no="SP2",
                    sku="SKU2",
                    quantity=Decimal("1"),
                    logistics_center_code="MISSING",
                ),
                ShipmentItem(
                    shipment_date="2026-07-06",
                    shipment_no="OW1",
                    sku="SKU3",
                    quantity=Decimal("1"),
                    logistics_center_code="ABE8",
                    source="overseas",
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "warehouse-region.xlsx"
            _write_mapping(path, [("美东", "ABE8")])

            loaded_rows, applied_rows, warning = apply_warehouse_region_mapping(raw, str(path))

        self.assertIsNone(warning)
        self.assertEqual(loaded_rows, 1)
        self.assertEqual(applied_rows, 1)
        self.assertEqual(raw.shipment_items[0].logistics_center_region, "美东")
        self.assertEqual(raw.shipment_items[1].logistics_center_region, "")
        self.assertEqual(raw.shipment_items[2].logistics_center_region, "")

    def test_missing_file_clears_amazon_region_without_warning_issue_data(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-07-06",
                    shipment_no="SP1",
                    sku="SKU1",
                    quantity=Decimal("1"),
                    logistics_center_code="ABE8",
                    logistics_center_region="Old",
                )
            ]
        )

        loaded_rows, applied_rows, warning = apply_warehouse_region_mapping(raw, "missing-warehouse-region.xlsx")

        self.assertEqual(loaded_rows, 0)
        self.assertEqual(applied_rows, 0)
        self.assertIn("warehouse region file not found", warning or "")
        self.assertEqual(raw.shipment_items[0].logistics_center_region, "")


def _write_mapping(path: Path, rows: list[tuple[str, str]]) -> None:
    write_xlsx_workbook(
        [("Sheet2", [("region", "分区"), ("warehouse", "仓库")], [{"region": region, "warehouse": warehouse} for region, warehouse in rows])],
        path,
    )


if __name__ == "__main__":
    unittest.main()
