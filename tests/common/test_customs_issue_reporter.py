from __future__ import annotations

import json
import unittest

from src.common.customs_issue_reporter import CustomsIssueReportConfig, report_customs_data_issues, stable_issue_key


class CustomsIssueReporterTest(unittest.TestCase):
    def test_disabled_reporter_does_not_call_urlopen(self) -> None:
        calls = []
        result = report_customs_data_issues(
            [{"issue_key": "product:1", "scope": "product"}],
            config=CustomsIssueReportConfig(enabled=False, url="https://example.test/import", token="token"),
            urlopen_func=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_enabled_reporter_posts_json_with_token(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"imported_count": 1, "resolved_count": 0, "active_count": 1})

        result = report_customs_data_issues(
            [{"issue_key": "product:1", "scope": "product", "field_name": "单位"}],
            replacement_scope={"scope": "product", "source": "product_sync"},
            config=CustomsIssueReportConfig(enabled=True, url="https://example.test/import", token="secret", timeout_seconds=7),
            urlopen_func=fake_urlopen,
        )

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-customs-issue-token"), "secret")
        self.assertEqual(payload["issues"][0]["field_name"], "单位")
        self.assertEqual(payload["replacement_scope"], {"scope": "product", "source": "product_sync"})
        self.assertEqual(result["imported_count"], 1)

    def test_stable_issue_key_is_short_and_repeatable(self) -> None:
        first = stable_issue_key("shipment", "amazon", "2026-08-17", "SP1", "SKU1", "单位")
        second = stable_issue_key("shipment", " amazon ", "2026-08-17", "SP1", "SKU1", "单位")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("shipment:"))
        self.assertLessEqual(len(first), 256)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
