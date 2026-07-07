from __future__ import annotations

import unittest
from decimal import Decimal

from src.shipment.build_rows import _box_count_from_box_no, _display_box_no, build_customs_workbook_data
from src.shipment.models import PurchaseBatch, RawCustomsData, ShipmentItem, SkuInfo
from src.shipment.sample_data import SampleDataSource


class BuildCustomsRowsTest(unittest.TestCase):
    def test_box_count_counts_numeric_ranges(self) -> None:
        self.assertEqual(_box_count_from_box_no("1-3"), Decimal("3"))
        self.assertEqual(_box_count_from_box_no("1,3-4"), Decimal("3"))

    def test_display_box_no_uses_commas_for_multiple_boxes(self) -> None:
        self.assertEqual(_display_box_no("FBA001\nFBA002\nFBA003"), "FBA001,FBA002,FBA003")
        self.assertEqual(_display_box_no("FBA001;FBA002/FBA003"), "FBA001,FBA002,FBA003")
        self.assertEqual(_display_box_no("FBA001"), "FBA001")

    def test_builds_row_with_logistics_center_region(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="SP1",
                    sku="SKU1",
                    quantity=Decimal("1"),
                    box_no="BOX1",
                    logistics_center_code="ABE8",
                    logistics_center_region="美东",
                    purchase_unit_price=Decimal("1"),
                    purchase_entity="Purchaser",
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
        )

        row = build_customs_workbook_data(raw).customs_rows[0]

        self.assertEqual(row.logistics_center_code, "ABE8")
        self.assertEqual(row.logistics_center_region, "美东")

    def test_builds_rows_split_by_purchase_batch(self) -> None:
        raw = SampleDataSource().load()

        workbook_data = build_customs_workbook_data(raw)

        sku_001_rows = [row for row in workbook_data.customs_rows if row.sku == "SKU-001"]
        self.assertEqual(len(sku_001_rows), 2)
        self.assertEqual(sum(row.shipment_quantity for row in sku_001_rows), Decimal("50"))
        self.assertEqual(len(workbook_data.purchase_split_rows), 3)
        self.assertEqual({row.package_type for row in workbook_data.customs_rows}, {"cnts"})
        self.assertEqual({row.trade_term for row in workbook_data.customs_rows}, {"FOB"})
        self.assertEqual({row.payment_method_name for row in workbook_data.customs_rows}, {"t/t"})
        self.assertEqual({row.currency for row in workbook_data.customs_rows}, {"美元"})
        self.assertEqual(sum(row.shipment_quantity for row in workbook_data.customs_rows), Decimal("60"))
        self.assertTrue(all(len(row.shipment_date) == 7 for row in workbook_data.customs_rows))
        self.assertTrue(all(row.id for row in workbook_data.customs_rows))

    def test_row_id_is_stable_for_shipment_sku_and_box(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(shipment_date="2026-05-01", shipment_no="SP1", sku="SKU1", quantity=Decimal("1"), box_no="BOX1"),
                ShipmentItem(shipment_date="2026-05-01", shipment_no="SP1", sku="SKU1", quantity=Decimal("1"), box_no="BOX1"),
                ShipmentItem(shipment_date="2026-05-01", shipment_no="SP1", sku="SKU1", quantity=Decimal("1"), box_no="BOX2"),
            ],
            sku_infos={
                "SKU1": SkuInfo(
                    sku="SKU1",
                    product_name="Product",
                    customs_name_cn="Customs",
                    unit="pcs",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10",
                )
            },
        )

        rows = build_customs_workbook_data(raw).customs_rows

        self.assertEqual(rows[0].id, rows[1].id)
        self.assertNotEqual(rows[0].id, rows[2].id)

    def test_infers_unit_but_not_customs_name_from_product_name_when_sku_info_missing(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-06-13",
                    shipment_no="SP260613017",
                    sku="251120469195",
                    quantity=Decimal("1"),
                    product_name="4条装6分打底裤 2XL-3XL",
                    box_no="BOX1",
                )
            ],
            sku_infos={
                "251120469195": SkuInfo(
                    sku="251120469195",
                    customs_name_cn="",
                    unit="",
                    gross_weight=Decimal("1"),
                    outer_box_size="1*1*1",
                )
            },
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].unit, "条")
        self.assertEqual(workbook_data.customs_rows[0].pieces, Decimal("4"))
        self.assertEqual(workbook_data.customs_rows[0].customs_name_cn, "待确认")

    def test_customs_row_uses_comma_separated_box_no_but_purchase_split_keeps_original(self) -> None:
        original_box_no = "FBA19FS69HZGU000001\nFBA19FS69HZGU000002\nFBA19FS69HZGU000003"
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-06-09",
                    shipment_no="SP260609001",
                    sku="241120367258",
                    quantity=Decimal("114"),
                    product_name="3条装产品",
                    box_no=original_box_no,
                    purchase_unit_price=Decimal("8.75"),
                )
            ],
            sku_infos={
                "241120367258": SkuInfo(
                    sku="241120367258",
                    product_name="3条装产品",
                    customs_name_cn="产品中文名",
                    unit="条",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="55*43*31",
                )
            },
            purchase_batches=[
                PurchaseBatch(
                    shipment_no="SP260609001",
                    sku="241120367258",
                    box_no=original_box_no,
                    quantity=Decimal("114"),
                    purchase_entity="采购主体",
                    supplier="供应商",
                    domestic_source="供应商地址",
                    purchase_order_no="PO1",
                    purchase_sn="PO1",
                    purchase_unit_price=Decimal("8.75"),
                )
            ],
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].box_no, "FBA19FS69HZGU000001,FBA19FS69HZGU000002,FBA19FS69HZGU000003")
        self.assertEqual(workbook_data.customs_rows[0].box_count, Decimal("3"))
        self.assertEqual(workbook_data.purchase_split_rows[0].box_no, original_box_no)

    def test_net_weight_uses_display_box_count(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-06-09",
                    shipment_no="SP260609001",
                    sku="SKU1",
                    quantity=Decimal("1"),
                    product_name="Product",
                    box_no="FBA001\nFBA002\nFBA003",
                    box_count=Decimal("1"),
                    total_gross_weight=Decimal("30"),
                    purchase_unit_price=Decimal("1"),
                    purchase_entity="Purchaser",
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
                    outer_box_size="10*10*10",
                )
            },
        )

        row = build_customs_workbook_data(raw).customs_rows[0]

        self.assertEqual(row.box_count, Decimal("3"))
        self.assertEqual(row.total_gross_weight, Decimal("30"))
        self.assertEqual(row.total_net_weight, Decimal("27"))

    def test_main_rows_are_unique_by_shipment_sku_and_box_when_batches_repeat(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-06-09",
                    shipment_no="SP260609033",
                    sku="240520456756",
                    quantity=Decimal("13"),
                    product_name="4pcs leggings L",
                    box_no="FBA19FSF3TP2U000013",
                    purchase_unit_price=Decimal("40"),
                )
            ],
            sku_infos={
                "240520456756": SkuInfo(
                    sku="240520456756",
                    product_name="4pcs leggings L",
                    customs_name_cn="leggings",
                    unit="pcs",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="52*42.02*32.01",
                )
            },
            purchase_batches=[
                PurchaseBatch(
                    shipment_no="SP260609033",
                    sku="240520456756",
                    box_no="FBA19FSF3TP2U000013",
                    quantity=Decimal("13"),
                    purchase_entity="Purchaser A",
                    supplier="Supplier A",
                    domestic_source="Yiwu",
                    purchase_order_no="PO260520001",
                    batch_no="2606090009-1",
                    purchase_unit_price=Decimal("40"),
                ),
                PurchaseBatch(
                    shipment_no="SP260609033",
                    sku="240520456756",
                    box_no="FBA19FSF3TP2U000013",
                    quantity=Decimal("13"),
                    purchase_entity="Purchaser A",
                    supplier="Supplier A",
                    domestic_source="Yiwu",
                    purchase_order_no="PO260520001",
                    batch_no="2606090026-1",
                    purchase_unit_price=Decimal("40"),
                ),
            ],
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(len(workbook_data.customs_rows), 1)
        self.assertEqual(workbook_data.customs_rows[0].shipment_quantity, Decimal("52"))
        self.assertEqual(workbook_data.customs_rows[0].purchase_entity, "Purchaser A")
        self.assertEqual(len(workbook_data.purchase_split_rows), 2)

    def test_missing_fba_stock_cost_marks_pending_and_issue(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("5"),
                    box_no="CTN-1",
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="产品X",
                    customs_name_cn="产品X中文名",
                    unit="个",
                    package_type="纸箱",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10cm",
                )
            },
            purchase_batches=[],
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].purchase_unit_price, "待确认")
        self.assertTrue(any(issue.field_name == "采购单价" for issue in workbook_data.issue_rows))
        self.assertTrue(any(issue.field_name == "采购主体" for issue in workbook_data.issue_rows))

    def test_uses_shipment_fba_stock_cost_without_purchase_batch(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("5"),
                    box_no="CTN-1",
                    purchase_unit_price=Decimal("8.88"),
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="产品X",
                    customs_name_cn="产品X中文名",
                    unit="个",
                    package_type="纸箱",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10cm",
                )
            },
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].purchase_unit_price, Decimal("8.88"))
        self.assertFalse(any(issue.field_name == "采购单价" for issue in workbook_data.issue_rows))

    def test_quantity_mismatch_creates_issue(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("5"),
                    box_no="CTN-1",
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="产品X",
                    customs_name_cn="产品X中文名",
                    unit="个",
                    package_type="纸箱",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10cm",
                )
            },
            purchase_batches=[
                PurchaseBatch(
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    box_no="CTN-1",
                    quantity=Decimal("4"),
                    supplier="供应商甲",
                    purchase_unit_price=Decimal("3.2"),
                )
            ],
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertTrue(any(issue.field_name == "发货数量" for issue in workbook_data.issue_rows))

    def test_missing_purchase_item_quantity_creates_issue(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("5"),
                    box_no="CTN-1",
                    purchase_unit_price=Decimal("8.88"),
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="产品X",
                    customs_name_cn="产品X中文名",
                    unit="个",
                    gross_weight=Decimal("1"),
                    net_weight=Decimal("0.8"),
                    outer_box_size="10*10*10cm",
                )
            },
            purchase_batches=[
                PurchaseBatch(
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    box_no="CTN-1",
                    quantity=Decimal("5"),
                    supplier="供应商甲",
                    purchase_entity="采购主体甲",
                    domestic_source="浙江义乌",
                    purchase_sn="PO-1",
                    purchase_unit_price=Decimal("8.88"),
                    quantity_missing=True,
                )
            ],
        )

        workbook_data = build_customs_workbook_data(raw)

        self.assertEqual(workbook_data.customs_rows[0].shipment_quantity, Decimal("5"))
        self.assertTrue(any(issue.field_name == "采购拆分数量" for issue in workbook_data.issue_rows))

    def test_detail_weight_size_and_volume_override_sku_calculation(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("5"),
                    product_name="3条装详情品名",
                    updated_at="2026-06-10 12:00:00",
                    box_no="CTN-1",
                    box_count=Decimal("2"),
                    volume=Decimal("0.123"),
                    total_gross_weight=Decimal("3.39"),
                    outer_box_size="20.70*18.50*1.90",
                    purchase_unit_price=Decimal("8.88"),
                    purchase_entity="采购主体甲",
                    supplier="供应商甲",
                    domestic_source="浙江义乌",
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="SKU品名",
                    customs_name_cn="产品X中文名",
                    unit="条",
                    gross_weight=Decimal("100"),
                    net_weight=Decimal("80"),
                    outer_box_size="10*10*10cm",
                    box_length_cm=Decimal("10"),
                    box_width_cm=Decimal("10"),
                    box_height_cm=Decimal("10"),
                )
            },
        )

        workbook_data = build_customs_workbook_data(raw)
        row = workbook_data.customs_rows[0]

        self.assertEqual(row.product_name, "3条装详情品名")
        self.assertEqual(row.shipment_date, "2026-05")
        self.assertEqual(row.shipment_day, "2026-05-01")
        self.assertEqual(row.pieces, Decimal("3"))
        self.assertEqual(row.shipment_quantity, Decimal("15"))
        self.assertEqual(row.updated_at, "2026-06-10 12:00:00")
        self.assertEqual(row.total_gross_weight, Decimal("3.39"))
        self.assertEqual(row.total_net_weight, Decimal("1.39"))
        self.assertEqual(row.outer_box_size, "20.70*18.50*1.90")
        self.assertEqual(row.volume, Decimal("0.123"))

    def test_detail_weight_and_volume_are_prorated_for_purchase_split(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    quantity=Decimal("10"),
                    box_no="CTN-1",
                    box_count=Decimal("2"),
                    volume=Decimal("0.50"),
                    total_gross_weight=Decimal("20"),
                    outer_box_size="20*10*5",
                )
            ],
            sku_infos={
                "SKU-X": SkuInfo(
                    sku="SKU-X",
                    product_name="产品X",
                    customs_name_cn="产品X中文名",
                    unit="个",
                )
            },
            purchase_batches=[
                PurchaseBatch(
                    shipment_no="FBA-001",
                    sku="SKU-X",
                    box_no="CTN-1",
                    quantity=Decimal("4"),
                    supplier="供应商甲",
                    purchase_entity="采购主体甲",
                    domestic_source="浙江义乌",
                    purchase_sn="PO-1",
                    purchase_unit_price=Decimal("8.88"),
                )
            ],
        )

        workbook_data = build_customs_workbook_data(raw)
        row = workbook_data.customs_rows[0]

        self.assertEqual(row.total_gross_weight, Decimal("20"))
        self.assertEqual(row.total_net_weight, Decimal("18"))
        self.assertEqual(row.volume, Decimal("0.50"))

    def test_duplicate_box_metrics_keep_only_first_sku_in_box(self) -> None:
        raw = RawCustomsData(
            shipment_items=[
                ShipmentItem(
                    shipment_date="2026-05-01",
                    shipment_no="SP260609039",
                    sku=sku,
                    quantity=Decimal("1"),
                    product_name="1条装产品",
                    box_no="FBA19FSJ3T1MU000001",
                    box_count=Decimal("1"),
                    volume=Decimal("0.07"),
                    total_gross_weight=Decimal("21.41"),
                    outer_box_size="55.00*43.01*30.99",
                    purchase_unit_price=Decimal("8.88"),
                    purchase_entity="采购主体甲",
                    supplier="供应商甲",
                    domestic_source="浙江义乌",
                )
                for sku in ("B-SKU", "A-SKU", "C-SKU")
            ],
            sku_infos={
                sku: SkuInfo(sku=sku, product_name="产品", customs_name_cn="产品中文名", unit="条")
                for sku in ("B-SKU", "A-SKU", "C-SKU")
            },
        )

        workbook_data = build_customs_workbook_data(raw)
        rows = workbook_data.customs_rows

        self.assertEqual([row.sku for row in rows], ["A-SKU", "B-SKU", "C-SKU"])
        self.assertEqual(rows[0].box_count, Decimal("1"))
        self.assertEqual(rows[0].total_gross_weight, Decimal("21.41"))
        self.assertEqual(rows[0].total_net_weight, Decimal("20.41"))
        self.assertEqual(rows[0].outer_box_size, "55.00*43.01*30.99")
        self.assertEqual(rows[0].volume, Decimal("0.07"))
        for row in rows[1:]:
            self.assertEqual(row.box_count, Decimal("0"))
            self.assertEqual(row.total_gross_weight, Decimal("0"))
            self.assertEqual(row.total_net_weight, Decimal("0"))
            self.assertEqual(row.outer_box_size, "0")
            self.assertEqual(row.volume, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
