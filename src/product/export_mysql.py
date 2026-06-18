from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from src.product.models import ProductRow
from src.shipment.export_mysql import MySQLConfig, MySQLExportError, _open_mysql_connection, _quote_identifier


PRODUCT_TABLE_COLUMNS = [
    ("sku", "sku"),
    ("product_name", "name"),
    ("material_cn", "report_element"),
    ("unit", "unit"),
    ("customs_name_cn", "chinese_customs_name"),
    ("customs_code", "customs_code"),
    ("update_time", "update_time"),
    ("is_enabled", "is_enabled"),
]


@dataclass(frozen=True)
class ProductMySQLResult:
    table: str
    total_rows: int
    inserted_rows: int
    updated_rows: int
    skipped_rows: int
    deleted_rows: int = 0


def export_products_to_mysql(
    rows: list[ProductRow],
    config: MySQLConfig | None = None,
    full_refresh: bool = False,
) -> ProductMySQLResult:
    config = config or MySQLConfig.from_env()
    table = os.getenv("MYSQL_PRODUCT_TABLE", "customs_product")
    unique_rows = _dedupe_by_sku(rows)
    insert_rows: list[ProductRow] = []
    update_rows: list[ProductRow] = []
    skipped_rows = 0
    deleted_rows = 0
    connection = None
    tunnel = None
    try:
        connection, tunnel = _open_mysql_connection(config)
        with connection.cursor() as cursor:
            _validate_product_table(cursor, table)
            if full_refresh:
                deleted_rows = _delete_existing_rows(cursor, table)
                existing = {}
            else:
                existing = _fetch_existing_update_times(cursor, table)
            insert_rows, update_rows, skipped_rows = _split_product_rows(unique_rows, existing)
            if insert_rows:
                cursor.executemany(_build_insert_sql(table), [_row_values(row) for row in insert_rows])
            if update_rows:
                cursor.executemany(_build_update_sql(table), [_update_values(row) for row in update_rows])
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
        if tunnel is not None:
            tunnel.stop()
    return ProductMySQLResult(
        table=table,
        total_rows=len(unique_rows),
        inserted_rows=len(insert_rows),
        updated_rows=len(update_rows),
        skipped_rows=skipped_rows,
        deleted_rows=deleted_rows,
    )


def _dedupe_by_sku(rows: Iterable[ProductRow]) -> list[ProductRow]:
    deduped: dict[str, ProductRow] = {}
    for row in rows:
        sku = str(row.sku or "").strip()
        if sku:
            deduped[sku] = row
    return list(deduped.values())


def _validate_product_table(cursor: Any, table: str) -> None:
    actual = {column.lower() for column in _fetch_table_columns(cursor, table)}
    required = [column for _, column in PRODUCT_TABLE_COLUMNS]
    missing = [column for column in required if column.lower() not in actual]
    if missing:
        raise MySQLExportError(f"{table} is missing columns: " + ", ".join(missing))


def _fetch_table_columns(cursor: Any, table: str) -> set[str]:
    cursor.execute(f"SHOW COLUMNS FROM {_quote_identifier(table)}")
    columns: set[str] = set()
    for row in cursor.fetchall():
        if isinstance(row, dict):
            field = row.get("Field")
        else:
            field = row[0] if row else None
        if field:
            columns.add(str(field))
    return columns


def _fetch_existing_update_times(cursor: Any, table: str) -> dict[str, str]:
    cursor.execute(f"SELECT `sku`, `update_time` FROM {_quote_identifier(table)}")
    existing: dict[str, str] = {}
    for row in cursor.fetchall():
        if isinstance(row, dict):
            sku = row.get("sku")
            update_time = row.get("update_time")
        else:
            sku = row[0] if len(row) > 0 else None
            update_time = row[1] if len(row) > 1 else None
        if sku:
            existing[str(sku)] = _normalize_update_time(update_time)
    return existing


def _delete_existing_rows(cursor: Any, table: str) -> int:
    cursor.execute(f"DELETE FROM {_quote_identifier(table)}")
    rowcount = getattr(cursor, "rowcount", 0)
    try:
        return int(rowcount)
    except (TypeError, ValueError):
        return 0


def _split_product_rows(rows: list[ProductRow], existing: dict[str, str]) -> tuple[list[ProductRow], list[ProductRow], int]:
    insert_rows: list[ProductRow] = []
    update_rows: list[ProductRow] = []
    skipped_rows = 0
    for row in rows:
        existing_update_time = existing.get(row.sku)
        row_update_time = _normalize_update_time(row.update_time)
        if existing_update_time is None:
            insert_rows.append(row)
        elif existing_update_time != row_update_time:
            update_rows.append(row)
        else:
            skipped_rows += 1
    return insert_rows, update_rows, skipped_rows


def _build_insert_sql(table: str) -> str:
    columns = [column for _, column in PRODUCT_TABLE_COLUMNS]
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {_quote_identifier(table)} ({column_sql}) VALUES ({placeholders})"


def _build_update_sql(table: str) -> str:
    assignments = ", ".join(
        f"{_quote_identifier(column)}=%s"
        for _, column in PRODUCT_TABLE_COLUMNS
        if column != "sku"
    )
    return f"UPDATE {_quote_identifier(table)} SET {assignments} WHERE `sku`=%s"


def _row_values(row: ProductRow) -> tuple[Any, ...]:
    return tuple(_value(getattr(row, attr)) for attr, _ in PRODUCT_TABLE_COLUMNS)


def _update_values(row: ProductRow) -> tuple[Any, ...]:
    values = [_value(getattr(row, attr)) for attr, column in PRODUCT_TABLE_COLUMNS if column != "sku"]
    values.append(row.sku)
    return tuple(values)


def _value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _normalize_update_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    return text[:19] if len(text) >= 19 and text[4:5] == "-" and text[7:8] == "-" else text
