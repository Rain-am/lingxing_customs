from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from src.common.xlsx_writer import write_xlsx_workbook
from src.shipment.models import CustomsWorkbookData


CUSTOMS_HEADERS = [
    ("id", "id"),
    ("shipment_date", "确定出运月份"),
    ("shipment_day", "确定出运日期"),
    ("shipment_no", "发货单号"),
    ("seller_name", "店铺"),
    ("purchase_entity", "采购主体"),
    ("supplier", "供应商"),
    ("domestic_source", "境内货源地"),
    ("sku", "物料编码（sku）"),
    ("pieces", "份数"),
    ("product_name", "品名"),
    ("customs_name_cn", "中文报关名"),
    ("customs_name_en", "英文报关品名"),
    ("unit", "单位"),
    ("shipment_quantity", "发货数量"),
    ("purchase_unit_price", "采购单价"),
    ("trade_term", "成交方式"),
    ("payment_method_name", "付款方式名称"),
    ("currency", "币别"),
    ("logistics_provider", "物流商"),
    ("logistics_channel", "物流渠道"),
    ("transport_method", "运输方式"),
    ("logistics_center_code", "物流中心编码"),
    ("logistics_center_region", "仓库分区"),
    ("package_type", "包装形式"),
    ("box_no", "箱号"),
    ("box_count", "箱数"),
    ("total_gross_weight", "单品总毛重"),
    ("total_net_weight", "单品总净重"),
    ("outer_box_size", "外箱尺寸"),
    ("volume", "体积"),
    ("updated_at", "更新时间"),
]

ISSUE_HEADERS = [
    ("shipment_no", "发货单号"),
    ("box_no", "箱号"),
    ("sku", "物料编码（sku）"),
    ("field_name", "字段"),
    ("issue", "问题"),
]

PURCHASE_SPLIT_HEADERS = [
    ("shipment_no", "发货单号"),
    ("box_no", "箱号"),
    ("sku", "物料编码（sku）"),
    ("purchase_order_no", "采购单号"),
    ("batch_no", "批次号"),
    ("supplier", "供应商"),
    ("purchase_entity", "采购主体"),
    ("quantity", "拆分数量"),
    ("purchase_unit_price", "采购单价"),
]


def export_customs_workbook(data: CustomsWorkbookData, output_path: Path) -> None:
    write_xlsx_workbook(
        [
            ("报关明细", CUSTOMS_HEADERS, [asdict(row) for row in data.customs_rows]),
            ("问题清单", ISSUE_HEADERS, [asdict(row) for row in data.issue_rows]),
            ("采购拆分明细", PURCHASE_SPLIT_HEADERS, [asdict(row) for row in data.purchase_split_rows]),
        ],
        output_path,
        text_keys={"id", "sku"},
    )
