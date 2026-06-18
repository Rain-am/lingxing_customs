from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

import src.product.export_mysql as export_mysql
from src.product.export_mysql import PRODUCT_TABLE_COLUMNS, export_products_to_mysql
from src.product.models import ProductRow


class ProductExportMySQLTest(unittest.TestCase):
    def test_export_inserts_updates_and_skips_by_sku_update_time(self) -> None:
        connection = FakeConnection(
            columns=[column for _, column in PRODUCT_TABLE_COLUMNS],
            existing=[
                ("SKU-SAME", datetime(2026, 6, 1, 10, 0, 0)),
                ("SKU-OLD", datetime(2026, 6, 1, 10, 0, 0)),
            ],
        )
        rows = [
            product_row("SKU-SAME", "2026-06-01 10:00:00"),
            product_row("SKU-OLD", "2026-06-02 10:00:00"),
            product_row("SKU-NEW", "2026-06-03 10:00:00"),
        ]

        with patch.object(export_mysql, "_open_mysql_connection", return_value=(connection, None)):
            result = export_products_to_mysql(rows, mysql_config())

        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.inserted_rows, 1)
        self.assertEqual(result.updated_rows, 1)
        self.assertEqual(result.skipped_rows, 1)
        self.assertEqual(len(connection.cursor_obj.executemany_calls), 2)
        insert_sql, insert_rows = connection.cursor_obj.executemany_calls[0]
        update_sql, update_rows = connection.cursor_obj.executemany_calls[1]
        self.assertIn("INSERT INTO `customs_product`", insert_sql)
        self.assertEqual(insert_rows[0][0], "SKU-NEW")
        self.assertEqual(insert_rows[0][-1], 1)
        self.assertIn("UPDATE `customs_product`", update_sql)
        self.assertIn("`is_enabled`=%s", update_sql)
        self.assertEqual(update_rows[0][-1], "SKU-OLD")

    def test_export_dedupes_duplicate_sku_before_writing(self) -> None:
        connection = FakeConnection(columns=[column for _, column in PRODUCT_TABLE_COLUMNS], existing=[])
        rows = [
            product_row("SKU-1", "2026-06-01 10:00:00", product_name="Old"),
            product_row("SKU-1", "2026-06-02 10:00:00", product_name="New"),
        ]

        with patch.object(export_mysql, "_open_mysql_connection", return_value=(connection, None)):
            result = export_products_to_mysql(rows, mysql_config())

        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.inserted_rows, 1)
        inserted_rows = connection.cursor_obj.executemany_calls[0][1]
        self.assertEqual(inserted_rows[0][1], "New")

    def test_full_refresh_deletes_existing_rows_before_insert(self) -> None:
        connection = FakeConnection(
            columns=[column for _, column in PRODUCT_TABLE_COLUMNS],
            existing=[("SKU-OLD", datetime(2026, 6, 1, 10, 0, 0))],
            delete_rowcount=7,
        )
        rows = [product_row("SKU-NEW", "2026-06-03 10:00:00")]

        with patch.object(export_mysql, "_open_mysql_connection", return_value=(connection, None)):
            result = export_products_to_mysql(rows, mysql_config(), full_refresh=True)

        self.assertEqual(result.deleted_rows, 7)
        self.assertIn("DELETE FROM `customs_product`", connection.cursor_obj.execute_calls)
        self.assertNotIn("SELECT `sku`, `update_time` FROM `customs_product`", connection.cursor_obj.execute_calls)
        self.assertEqual(result.inserted_rows, 1)

    def test_incremental_export_does_not_delete_existing_rows(self) -> None:
        connection = FakeConnection(columns=[column for _, column in PRODUCT_TABLE_COLUMNS], existing=[])

        with patch.object(export_mysql, "_open_mysql_connection", return_value=(connection, None)):
            export_products_to_mysql([product_row("SKU-NEW", "2026-06-03 10:00:00")], mysql_config())

        self.assertFalse(any(sql.startswith("DELETE FROM") for sql in connection.cursor_obj.execute_calls))


def product_row(sku: str, update_time: str, product_name: str = "Product") -> ProductRow:
    return ProductRow(
        sku=sku,
        product_name=product_name,
        material_cn="Cotton",
        unit="pcs",
        customs_name_cn="Clothing",
        customs_code="6109100000",
        update_time=update_time,
        is_enabled=1,
    )


def mysql_config():
    from src.shipment.export_mysql import MySQLConfig

    return MySQLConfig(
        host="172.31.0.4",
        port=3306,
        user="gmb001",
        password="secret",
        database="dtw_gmb000053",
    )


class FakeCursor:
    def __init__(self, columns, existing, delete_rowcount=0) -> None:
        self.columns = columns
        self.existing = existing
        self.last_sql = ""
        self.rowcount = 0
        self.delete_rowcount = delete_rowcount
        self.execute_calls = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql):
        self.last_sql = sql
        self.execute_calls.append(sql)
        self.rowcount = self.delete_rowcount if sql.startswith("DELETE FROM") else 0

    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, rows))

    def fetchall(self):
        if "SHOW COLUMNS" in self.last_sql:
            return [(column,) for column in self.columns]
        if "SELECT `sku`, `update_time`" in self.last_sql:
            return self.existing
        return []


class FakeConnection:
    def __init__(self, columns, existing, delete_rowcount=0) -> None:
        self.cursor_obj = FakeCursor(columns, existing, delete_rowcount=delete_rowcount)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


if __name__ == "__main__":
    unittest.main()
