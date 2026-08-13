import json
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


def original_schedule(**changes):
    value = {
        "id": 501,
        "processDefinitionCode": 9001,
        "processDefinitionName": "菲律宾-数仓工作流（1H）",
        "warningType": "NONE",
        "warningGroupId": 1,
        "failureStrategy": "END",
        "processInstancePriority": "HIGH",
        "workerGroup": "ph-data",
        "tenantCode": "dw_user",
        "environmentCode": 99,
        "releaseState": "ONLINE",
        "startParams": '[{"prop":"dt","value":"today"}]',
        "schedule": {
            "startTime": "2025-01-01 00:00:00",
            "endTime": "2099-12-31 23:59:59",
            "crontab": "0 0 * * * ?",
            "timezoneId": "Asia/Manila",
        },
    }
    value.update(changes)
    return value


class ScheduleAlertUpdateTests(unittest.TestCase):
    def test_rejects_invalid_warning_type_before_reading(self):
        client = FakeClient([])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "WHENEVER",
        })

        self.assertFalse(ok)
        self.assertEqual("INVALID_WARNING_TYPE", result["code"])
        self.assertEqual([], client.calls)

    def test_alert_only_update_preserves_complete_schedule(self):
        before = original_schedule()
        after = original_schedule(warningType="FAILURE", warningGroupId=42)
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": after}),
        ])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "failure",
            "warning_group_id": 42,
        })

        self.assertTrue(ok)
        self.assertEqual(["GET", "PUT", "GET"], [call["method"] for call in client.calls])
        form = client.calls[1]["form"]
        self.assertEqual("9001", str(form["processDefinitionCode"]))
        self.assertEqual("FAILURE", form["warningType"])
        self.assertEqual("42", str(form["warningGroupId"]))
        self.assertEqual("END", form["failureStrategy"])
        self.assertEqual("HIGH", form["processInstancePriority"])
        self.assertEqual("ph-data", form["workerGroup"])
        self.assertEqual("dw_user", form["tenantCode"])
        self.assertEqual("99", str(form["environmentCode"]))
        self.assertEqual("ONLINE", form["releaseState"])
        self.assertEqual(before["startParams"], form["startParams"])
        self.assertEqual(before["schedule"], json.loads(form["schedule"]))
        self.assertEqual("UPDATED", result["status"])
        self.assertEqual("ONLINE", result["release_state_before"])
        self.assertEqual("ONLINE", result["release_state_after"])
        self.assertNotIn("ds_token", json.dumps(result, ensure_ascii=False))
        self.assertEqual("NONE", result["rollback_payload"]["warning_type"])
        self.assertEqual(1, result["rollback_payload"]["warning_group_id"])

    def test_verification_failure_restores_original_schedule(self):
        before = original_schedule()
        corrupted = original_schedule(
            warningType="FAILURE",
            warningGroupId=42,
            schedule={**before["schedule"], "crontab": "0 30 * * * ?"},
        )
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": corrupted}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": before}),
        ])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "FAILURE",
            "warning_group_id": 42,
        })

        self.assertFalse(ok)
        self.assertEqual("VERIFICATION_FAILED_ROLLED_BACK", result["status"])
        self.assertEqual(["GET", "PUT", "GET", "PUT", "GET"], [call["method"] for call in client.calls])
        rollback_form = client.calls[3]["form"]
        self.assertEqual("NONE", rollback_form["warningType"])
        self.assertEqual(before["schedule"], json.loads(rollback_form["schedule"]))

    def test_warning_group_can_be_updated_without_warning_type(self):
        before = original_schedule(warningType="SUCCESS")
        after = original_schedule(warningType="SUCCESS", warningGroupId=42)
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": after}),
        ])

        ok, _ = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_group_id": 42,
        })

        self.assertTrue(ok)
        self.assertEqual("SUCCESS", client.calls[1]["form"]["warningType"])


if __name__ == "__main__":
    unittest.main()
