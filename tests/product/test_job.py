from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from src.product.job import _default_incremental_window, run_product_job


class ProductJobTest(unittest.TestCase):
    def test_default_incremental_window_is_yesterday_to_today(self) -> None:
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 18)

        with patch("src.product.job.date", FixedDate):
            self.assertEqual(_default_incremental_window(), ("2026-06-17", "2026-06-18"))

    def test_run_product_job_uses_explicit_update_time_window(self) -> None:
        data_source = FakeProductDataSource()
        args = SimpleNamespace(
            write_db=True,
            product_full_refresh=False,
            product_start_date="2026-06-17",
            product_end_date="2026-06-18",
        )

        with patch("src.product.job.ProductApiDataSource", return_value=data_source), patch(
            "src.product.job.export_products_to_mysql",
            return_value=SimpleNamespace(table="customs_product", deleted_rows=0, total_rows=0, inserted_rows=0, updated_rows=0, skipped_rows=0),
        ):
            run_product_job(args)

        self.assertEqual(data_source.load_all_calls, [("2026-06-17", "2026-06-18")])

    def test_run_product_job_uses_default_window_when_dates_omitted(self) -> None:
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 18)

        data_source = FakeProductDataSource()
        args = SimpleNamespace(write_db=True, product_full_refresh=False, product_start_date=None, product_end_date=None)

        with patch("src.product.job.date", FixedDate), patch("src.product.job.ProductApiDataSource", return_value=data_source), patch(
            "src.product.job.export_products_to_mysql",
            return_value=SimpleNamespace(table="customs_product", deleted_rows=0, total_rows=0, inserted_rows=0, updated_rows=0, skipped_rows=0),
        ):
            run_product_job(args)

        self.assertEqual(data_source.load_all_calls, [("2026-06-17", "2026-06-18")])


class FakeProductDataSource:
    def __init__(self) -> None:
        self.load_all_calls: list[tuple[str | None, str | None]] = []
        self.stats = SimpleNamespace(
            product_list_raw_rows=0,
            product_list_rows=0,
            products_without_id=0,
            enabled_products=0,
            skipped_not_enabled=0,
            detail_request_count=0,
            detail_missing=0,
            empty_status_rows=0,
            status_counts={},
        )

    def load_all(self, start_date: str | None = None, end_date: str | None = None) -> list:
        self.load_all_calls.append((start_date, end_date))
        return []


if __name__ == "__main__":
    unittest.main()
