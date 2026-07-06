from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.common.xlsx_writer import write_xlsx_workbook
from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.seller_department import (
    DIEXIANG_ENTITY,
    YACHANG_ENTITY,
    apply_seller_department_mapping,
    load_seller_department_mapping,
    purchase_entity_for_seller,
)
from src.shipment.models import PurchaseBatch, RawCustomsData, ShipmentItem, SkuInfo


class SellerDepartmentTest(unittest.TestCase):
    def test_purchase_entity_rules(self) -> None:
        mapping = {"SHOP-A": "业务二部", "SHOP-B": "业务一部", "SHOP-C": ""}

        self.assertEqual(purchase_entity_for_seller("SHOP-A", mapping), YACHANG_ENTITY)
        self.assertEqual(purchase_entity_for_seller("SHOP-B", mapping), DIEXIANG_ENTITY)
        self.assertEqual(purchase_entity_for_seller("SHOP-C", mapping), "")
        self.assertEqual(purchase_entity_for_seller("SHOP-MISSING", mapping), "")
        self.assertEqual(purchase_entity_for_seller("", mapping), "")

    def test_loads_excel_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shop-operations.xlsx"
            _write_mapping(path, [("SHOP-A", "业务二部"), ("SHOP-B", "业务一部")])

            mapping = load_seller_department_mapping(path)

        self.assertEqual(mapping, {"SHOP-A": "业务二部", "SHOP-B": "业务一部"})

    def test_applies_mapping_to_items_and_batches(self) -> None:
        raw = _raw_data(seller_name="SHOP-A", batch_entity="Old Purchaser")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shop-operations.xlsx"
            _write_mapping(path, [("SHOP-A", "业务二部")])

            loaded_rows, applied_rows, warning = apply_seller_department_mapping(raw, str(path))

        self.assertIsNone(warning)
        self.assertEqual(loaded_rows, 1)
        self.assertEqual(applied_rows, 1)
        self.assertEqual(raw.shipment_items[0].purchase_entity, YACHANG_ENTITY)
        self.assertEqual(raw.purchase_batches[0].purchase_entity, YACHANG_ENTITY)

        workbook_data = build_customs_workbook_data(raw)
        row = workbook_data.customs_rows[0]
        self.assertEqual(row.seller_name, "SHOP-A")
        self.assertEqual(row.purchase_entity, YACHANG_ENTITY)
        self.assertEqual(workbook_data.purchase_split_rows[0].purchase_entity, YACHANG_ENTITY)

    def test_empty_department_keeps_purchase_entity_blank_not_pending(self) -> None:
        raw = _raw_data(seller_name="SHOP-C", batch_entity="Old Purchaser")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shop-operations.xlsx"
            _write_mapping(path, [("SHOP-C", "")])

            apply_seller_department_mapping(raw, str(path))

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].purchase_entity, "")
        self.assertEqual(workbook_data.purchase_split_rows[0].purchase_entity, "")

    def test_missing_file_warns_and_clears_purchase_entities(self) -> None:
        raw = _raw_data(seller_name="SHOP-A", batch_entity="Old Purchaser")

        loaded_rows, applied_rows, warning = apply_seller_department_mapping(raw, "missing-shop-file.xlsx")

        self.assertEqual(loaded_rows, 0)
        self.assertEqual(applied_rows, 0)
        self.assertIn("seller department file not found", warning or "")
        self.assertEqual(raw.shipment_items[0].purchase_entity, "")
        self.assertEqual(raw.purchase_batches[0].purchase_entity, "")


def _raw_data(seller_name: str, batch_entity: str) -> RawCustomsData:
    return RawCustomsData(
        shipment_items=[
            ShipmentItem(
                shipment_date="2026-07-06",
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
                purchase_entity=batch_entity,
                supplier="Supplier",
                domestic_source="Source",
                purchase_order_no="PO1",
                purchase_sn="PO1",
                purchase_unit_price=Decimal("1.23"),
            )
        ],
    )


def _write_mapping(path: Path, rows: list[tuple[str, str]]) -> None:
    write_xlsx_workbook(
        [("店铺-运营", [("shop", "店铺"), ("department", "业务部门")], [{"shop": shop, "department": department} for shop, department in rows])],
        path,
    )


if __name__ == "__main__":
    unittest.main()
