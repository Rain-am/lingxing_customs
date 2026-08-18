from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.common.customs_issue_reporter import CustomsIssueReportConfig, report_customs_data_issues, stable_issue_key
from src.shipment.models import CustomsWorkbookData, IssueRow, RawCustomsData, ShipmentItem


PRODUCT_MASTER_FIELDS = {"中文报关品名", "单位", "品名", "单品毛重", "单品净重", "外箱尺寸", "海关编码", "中文材质"}
PURCHASE_SUPPLY_FIELDS = {"采购单价", "采购主体", "供应商", "境内货源地", "采购拆分数量", "采购单号"}
SHIPMENT_PACKING_FIELDS = {"箱号", "发货数量", "箱数", "体积", "物流中心编码", "仓库分区"}
CUSTOMER_MAPPING_FIELDS = {"最终客户"}


def report_shipment_customs_data_issues(
    raw_data: RawCustomsData,
    workbook_data: CustomsWorkbookData,
    *,
    shipment_times: Sequence[str],
    source_names: Sequence[str],
) -> dict[str, Any] | None:
    config = CustomsIssueReportConfig.from_env()
    if not config.enabled:
        return None
    try:
        issues = build_shipment_issue_payloads(raw_data, workbook_data, shipment_times=shipment_times, source_names=source_names)
        replacement_scopes = build_shipment_replacement_scopes(raw_data, shipment_times=shipment_times, source_names=source_names)
        print(f"Shipment customs issue rows ready: {len(issues)}")
        return report_customs_data_issues(issues, replacement_scopes=replacement_scopes, config=config)
    except Exception as exc:
        print(f"Warning: failed to build or report shipment customs data issues: {exc}")
        return None


def build_shipment_issue_payloads(
    raw_data: RawCustomsData,
    workbook_data: CustomsWorkbookData,
    *,
    shipment_times: Sequence[str],
    source_names: Sequence[str],
) -> list[dict[str, Any]]:
    item_index = _shipment_item_index(raw_data.shipment_items)
    row_index = _customs_row_index(workbook_data)
    payloads: list[dict[str, Any]] = []
    for issue in workbook_data.issue_rows:
        item = _find_issue_item(issue, item_index)
        row = _find_issue_customs_row(issue, row_index)
        field_name = _normalized_field_name(issue.field_name)
        source = _issue_source(issue, item, source_names)
        shipment_day = _issue_shipment_day(item, row, shipment_times)
        product_name = _issue_product_name(item, row)
        payloads.append(
            {
                "issue_key": stable_issue_key(
                    "shipment",
                    source,
                    shipment_day,
                    issue.shipment_no,
                    issue.box_no,
                    issue.sku,
                    field_name,
                    issue.issue,
                ),
                "scope": "shipment",
                "category": _issue_category(field_name),
                "severity": "high",
                "source": source,
                "shipment_day": shipment_day,
                "shipment_no": issue.shipment_no,
                "box_no": issue.box_no,
                "sku": issue.sku,
                "product_name": product_name,
                "field_name": field_name,
                "issue": issue.issue,
                "fix_hint": _fix_hint(field_name),
                "raw_json": _raw_issue_context(item),
            }
        )
    return payloads


def build_shipment_replacement_scopes(
    raw_data: RawCustomsData,
    *,
    shipment_times: Sequence[str],
    source_names: Sequence[str],
) -> list[dict[str, Any]]:
    keys: set[tuple[str, str]] = set()
    for source in source_names:
        for shipment_time in shipment_times:
            day = _shipment_day(shipment_time)
            if source and day:
                keys.add((str(source), day))
    for item in raw_data.shipment_items:
        source = str(item.source or "").strip()
        day = _shipment_day(item.shipment_date)
        if source and day:
            keys.add((source, day))
    return [{"scope": "shipment", "source": source, "shipment_day": day} for source, day in sorted(keys)]


def _shipment_item_index(items: Sequence[ShipmentItem]) -> dict[tuple[str, str, str], ShipmentItem]:
    index: dict[tuple[str, str, str], ShipmentItem] = {}
    for item in items:
        index[(item.shipment_no, item.box_no, item.sku)] = item
        index.setdefault((item.shipment_no, "", item.sku), item)
    return index


def _customs_row_index(workbook_data: CustomsWorkbookData) -> dict[tuple[str, str, str], Any]:
    index: dict[tuple[str, str, str], Any] = {}
    for row in workbook_data.customs_rows:
        index[(row.shipment_no, row.box_no, row.sku)] = row
        index.setdefault((row.shipment_no, "", row.sku), row)
    return index


def _find_issue_item(issue: IssueRow, index: Mapping[tuple[str, str, str], ShipmentItem]) -> ShipmentItem | None:
    return index.get((issue.shipment_no, issue.box_no, issue.sku)) or index.get((issue.shipment_no, "", issue.sku))


def _find_issue_customs_row(issue: IssueRow, index: Mapping[tuple[str, str, str], Any]) -> Any:
    return index.get((issue.shipment_no, issue.box_no, issue.sku)) or index.get((issue.shipment_no, "", issue.sku))


def _normalized_field_name(field_name: str) -> str:
    if field_name == "中文报关名":
        return "中文报关品名"
    return str(field_name or "").strip()


def _issue_category(field_name: str) -> str:
    if field_name in PRODUCT_MASTER_FIELDS:
        return "product_master"
    if field_name in PURCHASE_SUPPLY_FIELDS:
        return "purchase_supply"
    if field_name in SHIPMENT_PACKING_FIELDS:
        return "shipment_packing"
    if field_name in CUSTOMER_MAPPING_FIELDS:
        return "customer_mapping"
    return "other"


def _fix_hint(field_name: str) -> str:
    category = _issue_category(field_name)
    if category == "product_master":
        return "请在领星本地产品/报关资料中维护该字段，并等待物料表同步。"
    if category == "purchase_supply":
        return "请检查采购单、店铺映射或供应商资料维护。"
    if category == "shipment_packing":
        return "请检查发货单详情或装箱资料。"
    if category == "customer_mapping":
        return "请检查店铺-客户映射。"
    return "请按字段名称检查对应数据源维护。"


def _issue_source(issue: IssueRow, item: ShipmentItem | None, source_names: Sequence[str]) -> str:
    if item and item.source:
        return str(item.source).strip()
    shipment_no = str(issue.shipment_no or "").upper()
    if shipment_no.startswith("OWS"):
        return "overseas"
    if shipment_no.startswith(("SP", "FBA")):
        return "amazon"
    if len(source_names) == 1 and source_names[0]:
        return str(source_names[0])
    return "shipment_sync"


def _issue_shipment_day(item: ShipmentItem | None, row: Any, shipment_times: Sequence[str]) -> str:
    if item:
        day = _shipment_day(item.shipment_date)
        if day:
            return day
    if row is not None:
        day = _shipment_day(getattr(row, "shipment_day", ""))
        if day:
            return day
    if len(shipment_times) == 1:
        return _shipment_day(shipment_times[0])
    return ""


def _issue_product_name(item: ShipmentItem | None, row: Any) -> str:
    if item and item.product_name:
        return item.product_name
    if row is not None and getattr(row, "product_name", ""):
        return str(getattr(row, "product_name"))
    return ""


def _raw_issue_context(item: ShipmentItem | None) -> dict[str, Any]:
    if item is None:
        return {}
    return {
        "shipment_date": item.shipment_date,
        "seller_name": item.seller_name,
        "dest_country": item.dest_country,
        "final_customer": item.final_customer,
        "purchase_entity": item.purchase_entity,
        "supplier": item.supplier,
        "domestic_source": item.domestic_source,
        "source": item.source,
    }


def _shipment_day(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""
