from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from src.shipment.build_rows import build_customs_workbook_data
from src.shipment.export_excel import export_customs_workbook
from src.shipment.sample_data import SampleDataSource


class ExportExcelTest(unittest.TestCase):
    def test_exports_expected_sheets(self) -> None:
        raw = SampleDataSource().load()
        workbook_data = build_customs_workbook_data(raw)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "customs.xlsx"

            export_customs_workbook(workbook_data, output_path)

            self.assertTrue(output_path.exists())
            with ZipFile(output_path) as archive:
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                styles_xml = archive.read("xl/styles.xml").decode("utf-8")
            self.assertEqual(workbook_xml.count("<sheet "), 3)
            self.assertIn("<sheetData>", sheet_xml)
            self.assertIn('r="A1"', sheet_xml)
            self.assertIn('formatCode="yyyy-mm-dd"', styles_xml)
            self.assertIn('<c r="A1" t="inlineStr" s="1"><is><t>id</t></is></c>', sheet_xml)
            self.assertIn('<c r="B1" t="inlineStr" s="1"><is><t>确定出运月份</t></is></c>', sheet_xml)
            self.assertIn('<c r="C1" t="inlineStr" s="1"><is><t>确定出运日期</t></is></c>', sheet_xml)
            self.assertIn('<c r="B2" t="inlineStr"><is><t>2026-', sheet_xml)
            self.assertIn('r="A2" t="inlineStr" s="3"', sheet_xml)
            self.assertIn('<c r="E1" t="inlineStr" s="1"><is><t>店铺</t></is></c>', sheet_xml)
            self.assertIn('r="I2" t="inlineStr" s="3"', sheet_xml)
            self.assertIn('<c r="M1" t="inlineStr" s="1"><is><t>英文报关品名</t></is></c>', sheet_xml)
            self.assertIn('<c r="U1" t="inlineStr" s="1"><is><t>物流渠道</t></is></c>', sheet_xml)
            self.assertIn('<c r="V1" t="inlineStr" s="1"><is><t>运输方式</t></is></c>', sheet_xml)
            self.assertIn('<c r="X1" t="inlineStr" s="1"><is><t>仓库分区</t></is></c>', sheet_xml)
            self.assertNotIn("物流方式", sheet_xml)
            self.assertIn('<c r="AF1" t="inlineStr" s="1"><is><t>更新时间</t></is></c>', sheet_xml)


if __name__ == "__main__":
    unittest.main()
