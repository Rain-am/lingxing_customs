from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.common.customs_issue_reporter import CustomsIssueReportConfig, report_customs_data_issues, stable_issue_key
from src.shipment.export_mysql import MySQLConfig, _open_mysql_connection, _quote_identifier
from src.shipment.models import CustomsWorkbookData, IssueRow, RawCustomsData, ShipmentItem


PRODUCT_MASTER_FIELDS = {"中文报关品名", "单位", "品名", "单品毛重", "单品净重", "外箱尺寸", "海关编码", "中文材质"}
PURCHASE_SUPPLY_FIELDS = {"采购单价", "采购主体", "供应商", "境内货源地", "采购拆分数量", "采购单号"}
SHIPMENT_PACKING_FIELDS = {"箱号", "发货数量", "箱数", "体积", "物流中心编码", "仓库分区"}
CUSTOMER_MAPPING_FIELDS = {"最终客户"}
SPLIT_SUPPLIER_ISSUE = "供应商名称存在多个候选，请确认采购单供应商"
SPLIT_SUPPLIER_SELECT_COLUMNS = [
    "id",
    "confirm_shipment",
    "tran_id",
    "seller_name",
    "final_cust",
    "item_code",
    "name",
    "box_no",
    "supplier_name",
    "supplier_addr",
]


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
        try:
            issues.extend(load_split_supplier_issue_payloads(shipment_times=shipment_times, source_names=source_names))
        except Exception as exc:
            print(f"Warning: failed to load split supplier shipment issues from MySQL: {exc}")
        issues = _dedupe_issue_payloads(issues)
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


def load_split_supplier_issue_payloads(
    config: MySQLConfig | None = None,
    table: str | None = None,
    *,
    shipment_times: Sequence[str],
    source_names: Sequence[str],
) -> list[dict[str, Any]]:
    days = sorted({_shipment_day(shipment_time) for shipment_time in shipment_times if _shipment_day(shipment_time)})
    tran_id_patterns = sorted({_source_tran_id_pattern(source) for source in source_names if _source_tran_id_pattern(source)})
    if not days or not tran_id_patterns:
        return []
    config = config or MySQLConfig.from_env()
    table = table or config.table
    rows = _fetch_split_supplier_rows(config, table, days=days, tran_id_patterns=tran_id_patterns)
    return build_split_supplier_issue_payloads(rows, source_names=source_names)


def build_split_supplier_issue_payloads(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_names: Sequence[str],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for row in rows:
        supplier_name = _text(row.get("supplier_name"))
        if "/" not in supplier_name:
            continue
        source = _source_from_tran_id(_text(row.get("tran_id")), source_names)
        shipment_day = _shipment_day(row.get("confirm_shipment"))
        shipment_no = _text(row.get("tran_id"))
        box_no = _text(row.get("box_no"))
        sku = _text(row.get("item_code"))
        field_name = "供应商"
        payloads.append(
            {
                "issue_key": stable_issue_key(
                    "shipment",
                    source,
                    shipment_day,
                    shipment_no,
                    box_no,
                    sku,
                    field_name,
                    SPLIT_SUPPLIER_ISSUE,
                ),
                "scope": "shipment",
                "category": _issue_category(field_name),
                "severity": "high",
                "source": source,
                "shipment_day": shipment_day,
                "shipment_no": shipment_no,
                "box_no": box_no,
                "sku": sku,
                "product_name": _text(row.get("name")),
                "field_name": field_name,
                "issue": SPLIT_SUPPLIER_ISSUE,
                "fix_hint": _fix_hint(field_name),
                "raw_json": {
                    "id": _text(row.get("id")),
                    "seller_name": _text(row.get("seller_name")),
                    "final_customer": _text(row.get("final_cust")),
                    "supplier_name": supplier_name,
                    "supplier_addr": _text(row.get("supplier_addr")),
                },
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


def _fetch_split_supplier_rows(
    config: MySQLConfig,
    table: str,
    *,
    days: Sequence[str],
    tran_id_patterns: Sequence[str],
) -> list[dict[str, Any]]:
    connection = None
    tunnel = None
    try:
        connection, tunnel = _open_mysql_connection(config)
        with connection.cursor() as cursor:
            cursor.execute(_build_select_split_suppliers_sql(table, len(days), len(tran_id_patterns)), ["%/%", *days, *tran_id_patterns])
            return [_split_supplier_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        if connection is not None:
            connection.close()
        if tunnel is not None:
            tunnel.stop()


def _build_select_split_suppliers_sql(table: str, day_count: int, pattern_count: int) -> str:
    if day_count <= 0 or pattern_count <= 0:
        return ""
    columns = ", ".join(_quote_identifier(column) for column in SPLIT_SUPPLIER_SELECT_COLUMNS)
    day_placeholders = ", ".join(["%s"] * day_count)
    source_filter = " OR ".join(["`tran_id` LIKE %s"] * pattern_count)
    return (
        f"SELECT {columns} FROM {_quote_identifier(table)} "
        "WHERE `supplier_name` LIKE %s "
        f"AND `confirm_shipment` IN ({day_placeholders}) "
        f"AND ({source_filter}) "
        "ORDER BY `confirm_shipment`, `tran_id`, `item_code`, `box_no`, `id`"
    )


def _split_supplier_row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return {column: row.get(column) for column in SPLIT_SUPPLIER_SELECT_COLUMNS}
    return {column: row[index] if len(row) > index else None for index, column in enumerate(SPLIT_SUPPLIER_SELECT_COLUMNS)}


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


def _dedupe_issue_payloads(payloads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        issue_key = str(payload.get("issue_key") or "")
        if issue_key in seen:
            continue
        seen.add(issue_key)
        deduped.append(payload)
    return deduped


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


def _source_from_tran_id(tran_id: str, source_names: Sequence[str]) -> str:
    shipment_no = str(tran_id or "").upper()
    if shipment_no.startswith("OWS"):
        return "overseas"
    if shipment_no.startswith(("SP", "FBA")):
        return "amazon"
    if len(source_names) == 1 and source_names[0]:
        return str(source_names[0])
    return "shipment_sync"


def _source_tran_id_pattern(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized == "amazon":
        return "SP%"
    if normalized == "overseas":
        return "OWS%"
    return ""


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
