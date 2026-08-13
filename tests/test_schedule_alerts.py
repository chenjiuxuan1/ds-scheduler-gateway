import unittest

from clients.dolphinscheduler_client import DolphinSchedulerClient
from gateway.models import CountryConfig
from gateway.utils import SUPPORTED_ACTIONS
from handlers.workflow_handlers import dispatch_action


def config(**kwargs):
    values = {
        "country": "ph",
        "base_url": "http://example.invalid",
        "project_code": "1",
    }
    values.update(kwargs)
    return CountryConfig(**values)


class FakeClient(DolphinSchedulerClient):
    def __init__(self, responses):
        super().__init__(config(), "test-token")
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, query=None, form=None, json_body=None):
        self.calls.append({
            "method": method,
            "path": path,
            "query": query,
            "form": form,
            "json_body": json_body,
        })
        return self.responses.pop(0)


class AlertGroupLookupTests(unittest.TestCase):
    def test_new_actions_are_supported(self):
        self.assertIn("list_alert_groups", SUPPORTED_ACTIONS)
        self.assertIn("batch_update_schedule_alerts", SUPPORTED_ACTIONS)

    def test_dispatches_list_alert_groups(self):
        client = FakeClient([
            (True, {"code": 0, "data": {"totalList": []}}),
        ])

        ok, result = dispatch_action(client, "list_alert_groups", {})

        self.assertTrue(ok)
        self.assertEqual([], result["items"])

    def test_lists_and_normalizes_alert_groups(self):
        client = FakeClient([
            (True, {"code": 0, "data": {"total": 1, "totalList": [{
                "id": 42,
                "groupName": "platform alerts",
                "description": "owned by platform",
                "alertInstanceIds": [3, 4],
            }]}}),
        ])

        ok, result = client.list_alert_groups({"page_no": 2, "page_size": 50})

        self.assertTrue(ok)
        self.assertEqual({"pageNo": 2, "pageSize": 50, "searchVal": ""}, client.calls[0]["query"])
        self.assertEqual("/alert-groups", client.calls[0]["path"])
        self.assertEqual(42, result["items"][0]["id"])
        self.assertEqual("platform alerts", result["items"][0]["group_name"])
        self.assertEqual("owned by platform", result["items"][0]["description"])
        self.assertEqual([3, 4], result["items"][0]["alert_instances"])
        self.assertEqual("platform alerts", result["items"][0]["raw"]["groupName"])

    def test_exact_alert_group_returns_unique_match(self):
        client = FakeClient([
            (True, {"code": 0, "data": {"totalList": [
                {"id": 41, "groupName": "n8n告警触发器-old"},
                {"id": 42, "groupName": "n8n告警触发器"},
            ]}}),
        ])

        ok, result = client.list_alert_groups({"search_val": "n8n告警触发器"})

        self.assertTrue(ok)
        self.assertEqual(42, result["id"])
        self.assertEqual("n8n告警触发器", result["group_name"])

    def test_exact_alert_group_rejects_no_match(self):
        client = FakeClient([
            (True, {"code": 0, "data": {"totalList": [
                {"id": 41, "groupName": "n8n告警触发器-old"},
            ]}}),
        ])

        ok, result = client.list_alert_groups({"search_val": "n8n告警触发器"})

        self.assertFalse(ok)
        self.assertEqual("ALERT_GROUP_NOT_FOUND", result["code"])

    def test_exact_alert_group_rejects_duplicates(self):
        client = FakeClient([
            (True, {"code": 0, "data": {"totalList": [
                {"id": 7, "groupName": "n8n告警触发器"},
                {"id": 8, "groupName": "n8n告警触发器"},
            ]}}),
        ])

        ok, result = client.list_alert_groups({"search_val": "n8n告警触发器"})

        self.assertFalse(ok)
        self.assertEqual("AMBIGUOUS_ALERT_GROUP", result["code"])
        self.assertEqual([7, 8], [item["id"] for item in result["candidates"]])


if __name__ == "__main__":
    unittest.main()
