from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

from src.common.customs_issue_reporter import CustomsIssueReportConfig, report_customs_data_issues, stable_issue_key
from src.shipment.export_mysql import MySQLConfig, _open_mysql_connection, _quote_identifier


PRODUCT_ISSUE_SOURCE = "product_sync"
REQUIRED_PRODUCT_FIELDS = [
    ("name", "品名"),
    ("chinese_customs_name", "中文报关品名"),
    ("unit", "单位"),
    ("customs_code", "海关编码"),
    ("report_element", "中文材质"),
]
PRODUCT_SELECT_COLUMNS = [
    "sku",
    "name",
    "report_element",
    "unit",
    "chinese_customs_name",
    "customs_code",
    "update_time",
    "is_enabled",
]


def report_product_customs_data_issues() -> dict[str, Any] | None:
    config = CustomsIssueReportConfig.from_env()
    if not config.enabled:
        return None
    try:
        issues = load_active_product_issue_payloads()
        print(f"Product customs issue rows ready: {len(issues)}")
        return report_customs_data_issues(
            issues,
            replacement_scope={"scope": "product", "source": PRODUCT_ISSUE_SOURCE},
            config=config,
        )
    except Exception as exc:
        print(f"Warning: failed to build or report product customs data issues: {exc}")
        return None


def load_active_product_issue_payloads(config: MySQLConfig | None = None, table: str | None = None) -> list[dict[str, Any]]:
    config = config or MySQLConfig.from_env()
    table = table or os.getenv("MYSQL_PRODUCT_TABLE", "customs_product")
    rows = _fetch_active_product_rows(config, table)
    return build_product_issue_payloads(rows)


def build_product_issue_payloads(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in rows:
        sku = _text(row.get("sku"))
        if not sku:
            continue
        product_name = _text(row.get("name"))
        for column, field_name in REQUIRED_PRODUCT_FIELDS:
            if not _missing(row.get(column)):
                continue
            issues.append(
                {
                    "issue_key": stable_issue_key("product", PRODUCT_ISSUE_SOURCE, sku, field_name),
                    "scope": "product",
                    "category": "product_master",
                    "severity": "high",
                    "source": PRODUCT_ISSUE_SOURCE,
                    "sku": sku,
                    "product_name": product_name,
                    "field_name": field_name,
                    "issue": f"产品资料缺失：{field_name}",
                    "fix_hint": "请在领星本地产品/报关资料中维护该字段，并等待物料表同步。",
                    "raw_json": {
                        "sku": sku,
                        "column": column,
                        "update_time": _text(row.get("update_time")),
                    },
                }
            )
    return issues


def _fetch_active_product_rows(config: MySQLConfig, table: str) -> list[dict[str, Any]]:
    connection = None
    tunnel = None
    try:
        connection, tunnel = _open_mysql_connection(config)
        with connection.cursor() as cursor:
            cursor.execute(_build_select_active_products_sql(table))
            return [_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        if connection is not None:
            connection.close()
        if tunnel is not None:
            tunnel.stop()


def _build_select_active_products_sql(table: str) -> str:
    columns = ", ".join(_quote_identifier(column) for column in PRODUCT_SELECT_COLUMNS)
    return f"SELECT {columns} FROM {_quote_identifier(table)} WHERE `is_enabled` = 1"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return {column: row.get(column) for column in PRODUCT_SELECT_COLUMNS}
    return {column: row[index] if len(row) > index else None for index, column in enumerate(PRODUCT_SELECT_COLUMNS)}


def _missing(value: Any) -> bool:
    return _text(value) == ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
