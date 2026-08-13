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

    def test_returned_rollback_payload_rebuilds_full_original_form(self):
        before = original_schedule()
        after = original_schedule(warningType="FAILURE", warningGroupId=42)
        update_client = FakeClient([
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": after}),
        ])
        ok, result = update_client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "FAILURE",
            "warning_group_id": 42,
        })
        self.assertTrue(ok)

        rollback_client = FakeClient([(True, {"code": 0, "data": True})])
        rollback_ok, _ = rollback_client.update_schedule(result["rollback_payload"])

        self.assertTrue(rollback_ok)
        form = rollback_client.calls[0]["form"]
        self.assertEqual("NONE", form["warningType"])
        self.assertEqual("1", str(form["warningGroupId"]))
        self.assertEqual(before["schedule"], json.loads(form["schedule"]))
        self.assertEqual(before["startParams"], form["startParams"])
        self.assertEqual("ONLINE", form["releaseState"])

    def test_transient_uncertain_write_reads_state_before_retry(self):
        before = original_schedule()
        after = original_schedule(warningType="FAILURE", warningGroupId=42)
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (False, {"status": 503, "body": {"message": "timeout"}}),
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": after}),
        ])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "FAILURE",
            "warning_group_id": 42,
            "retry_attempts": 2,
            "retry_delay_ms": 0,
        })

        self.assertTrue(ok)
        self.assertEqual("UPDATED", result["status"])
        self.assertEqual(["GET", "PUT", "GET", "PUT", "GET"], [call["method"] for call in client.calls])

    def test_failed_write_with_partial_change_is_rolled_back(self):
        before = original_schedule()
        partial = original_schedule(warningType="FAILURE", warningGroupId=1)
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (False, {"status": 400, "body": {"message": "rejected after partial write"}}),
            (True, {"code": 0, "data": partial}),
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
        self.assertEqual("FAILED_ROLLED_BACK", result["status"])
        self.assertEqual(["GET", "PUT", "GET", "PUT", "GET"], [call["method"] for call in client.calls])

    def test_non_transient_failed_write_is_not_retried_when_unchanged(self):
        before = original_schedule()
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (False, {"status": 400, "body": {"message": "validation error"}}),
            (True, {"code": 0, "data": before}),
        ])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "FAILURE",
            "warning_group_id": 42,
            "retry_attempts": 5,
            "retry_delay_ms": 0,
        })

        self.assertFalse(ok)
        self.assertEqual("FAILED_UNCHANGED", result["status"])
        self.assertEqual(1, sum(call["method"] == "PUT" for call in client.calls))

    def test_failed_rollback_is_reported_explicitly(self):
        before = original_schedule()
        corrupted = original_schedule(
            warningType="FAILURE",
            warningGroupId=42,
            workerGroup="wrong-worker",
        )
        client = FakeClient([
            (True, {"code": 0, "data": before}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": corrupted}),
            (False, {"status": 400, "body": {"message": "rollback rejected"}}),
            (True, {"code": 0, "data": corrupted}),
        ])

        ok, result = client.update_schedule({
            "project_code": "100",
            "schedule_id": "501",
            "warning_type": "FAILURE",
            "warning_group_id": 42,
        })

        self.assertFalse(ok)
        self.assertEqual("FAILED_ROLLBACK_FAILED", result["status"])
        self.assertIn("worker_group", result["rollback_mismatches"])


class FixtureBatchClient(DolphinSchedulerClient):
    def __init__(self, projects=None, alert_group=None, workflows=None, schedules=None):
        super().__init__(config(), "batch-test-token")
        self.projects = projects or {}
        self.alert_group = alert_group or {
            "id": 42,
            "group_name": "n8n告警触发器",
            "description": "",
            "alert_instances": [3],
            "raw": {},
        }
        self.workflows = workflows or {}
        self.schedules = schedules or {}
        self.calls = []

    def resolve_project(self, payload):
        name = payload.get("project_name")
        self.calls.append(("resolve_project", name))
        value = self.projects.get(name)
        if isinstance(value, tuple):
            return value
        if not value:
            return False, {"code": "PROJECT_NOT_FOUND", "message": name}
        return True, {"project_code": value, "project_name": name}

    def list_alert_groups(self, payload):
        self.calls.append(("list_alert_groups", payload.get("search_val")))
        if isinstance(self.alert_group, tuple):
            return self.alert_group
        return True, self.alert_group

    def list_workflows(self, payload):
        project_code = str(payload.get("project_code"))
        self.calls.append(("list_workflows", project_code, payload.get("page_no")))
        items = self.workflows.get(project_code, [])
        if isinstance(items, tuple):
            return items
        return True, {"code": 0, "data": {"total": len(items), "totalList": items}}

    def list_schedules(self, payload):
        project_code = str(payload.get("project_code"))
        self.calls.append(("list_schedules", project_code, payload.get("page_no")))
        items = self.schedules.get(project_code, [])
        if isinstance(items, tuple):
            return items
        return True, {"code": 0, "data": {"total": len(items), "totalList": items}}

    def request(self, method, path, query=None, form=None, json_body=None):
        self.calls.append((method, path, form))
        raise AssertionError(f"dry run attempted transport call: {method} {path}")


class BatchScheduleAlertTests(unittest.TestCase):
    def test_rejects_invalid_batch_input_before_lookup(self):
        client = FixtureBatchClient()

        ok, result = client.batch_update_schedule_alerts({
            "project_names": [],
            "warning_type": "FAILURE",
            "warning_group_name": "n8n告警触发器",
            "dry_run": True,
        })

        self.assertFalse(ok)
        self.assertEqual("INVALID_PROJECT_NAMES", result["code"])
        self.assertEqual([], client.calls)

    def test_preflight_failure_stops_country_before_schedule_discovery(self):
        client = FixtureBatchClient(
            projects={"DW_DWB": "100", "DW_DWD": (
                False,
                {"code": "AMBIGUOUS_PROJECT", "message": "duplicate"},
            )},
        )

        ok, result = client.batch_update_schedule_alerts({
            "project_names": ["DW_DWB", "DW_DWD"],
            "workflow_release_state": "ONLINE",
            "schedule_release_state": "ONLINE",
            "warning_type": "FAILURE",
            "warning_group_name": "n8n告警触发器",
            "dry_run": True,
        })

        self.assertFalse(ok)
        self.assertEqual("BATCH_PREFLIGHT_FAILED", result["code"])
        self.assertTrue(any(error["code"] == "AMBIGUOUS_PROJECT" for error in result["errors"]))
        self.assertFalse(any(call[0] == "list_workflows" for call in client.calls))
        self.assertFalse(any(call[0] in {"PUT", "POST"} for call in client.calls))

    def test_dry_run_selects_only_online_workflow_and_schedule(self):
        client = FixtureBatchClient(
            projects={"DW_DWB": "100", "DW_DWD": "200"},
            workflows={
                "100": [
                    {"code": 9001, "name": "online-needs-change", "releaseState": "ONLINE"},
                    {"code": 9002, "name": "offline-workflow", "releaseState": "OFFLINE"},
                    {"code": 9003, "name": "offline-schedule", "releaseState": "ONLINE"},
                ],
                "200": [
                    {"code": 9101, "name": "already-matched", "releaseState": "ONLINE"},
                ],
            },
            schedules={
                "100": [
                    original_schedule(
                        id=501,
                        processDefinitionCode=9001,
                        processDefinitionName="online-needs-change",
                    ),
                    original_schedule(
                        id=502,
                        processDefinitionCode=9002,
                        processDefinitionName="offline-workflow",
                    ),
                    original_schedule(
                        id=503,
                        processDefinitionCode=9003,
                        processDefinitionName="offline-schedule",
                        releaseState="OFFLINE",
                    ),
                ],
                "200": [
                    original_schedule(
                        id=601,
                        processDefinitionCode=9101,
                        processDefinitionName="already-matched",
                        warningType="FAILURE",
                        warningGroupId=42,
                    ),
                ],
            },
        )

        ok, result = client.batch_update_schedule_alerts({
            "project_names": ["DW_DWB", "DW_DWD"],
            "workflow_release_state": "ONLINE",
            "schedule_release_state": "ONLINE",
            "warning_type": "failure",
            "warning_group_name": "n8n告警触发器",
            "dry_run": True,
        })

        self.assertTrue(ok)
        self.assertEqual("ph", result["country"])
        self.assertEqual({
            "total": 4,
            "matched": 1,
            "updated": 0,
            "skipped": 3,
            "failed": 0,
            "verification_failed": 0,
            "rolled_back": 0,
            "rollback_failed": 0,
        }, result["summary"])
        by_id = {str(item["schedule_id"]): item for item in result["results"]}
        self.assertEqual("DRY_RUN_MATCHED", by_id["501"]["status"])
        self.assertEqual("SKIPPED_NOT_ONLINE", by_id["502"]["status"])
        self.assertEqual("SKIPPED_NOT_ONLINE", by_id["503"]["status"])
        self.assertEqual("SKIPPED_ALREADY_MATCHED", by_id["601"]["status"])
        self.assertEqual("DW_DWB", by_id["501"]["project_name"])
        self.assertEqual("100", by_id["501"]["project_code"])
        self.assertEqual("online-needs-change", by_id["501"]["workflow_name"])
        self.assertEqual("9001", str(by_id["501"]["workflow_code"]))
        self.assertEqual("NONE", by_id["501"]["original_warning_type"])
        self.assertEqual(1, by_id["501"]["original_warning_group_id"])
        self.assertEqual("FAILURE", by_id["501"]["target_warning_type"])
        self.assertEqual(42, by_id["501"]["target_warning_group_id"])
        self.assertNotIn("batch-test-token", json.dumps(result, ensure_ascii=False))
        self.assertFalse(any(call[0] in {"PUT", "POST"} for call in client.calls))

    def test_project_discovery_failure_prevents_all_country_writes(self):
        client = FixtureBatchClient(
            projects={"DW_DWB": "100", "DW_DWD": "200"},
            workflows={
                "100": [{"code": 9001, "name": "ready", "releaseState": "ONLINE"}],
                "200": (False, {"status": 503, "message": "temporarily unavailable"}),
            },
            schedules={
                "100": [original_schedule(processDefinitionCode=9001)],
                "200": [],
            },
        )

        ok, result = client.batch_update_schedule_alerts({
            "project_names": ["DW_DWB", "DW_DWD"],
            "workflow_release_state": "ONLINE",
            "schedule_release_state": "ONLINE",
            "warning_type": "FAILURE",
            "warning_group_name": "n8n告警触发器",
            "dry_run": False,
        })

        self.assertFalse(ok)
        self.assertEqual("BATCH_PREFLIGHT_FAILED", result["code"])
        self.assertFalse(any(call[0] in {"PUT", "POST"} for call in client.calls))


class MutationBatchClient(FixtureBatchClient):
    def __init__(self, outcomes):
        super().__init__(
            projects={"DW_DWB": "100"},
            workflows={"100": [
                {"code": 9001, "name": "first", "releaseState": "ONLINE"},
                {"code": 9002, "name": "second", "releaseState": "ONLINE"},
            ]},
            schedules={"100": [
                original_schedule(id=501, processDefinitionCode=9001, processDefinitionName="first"),
                original_schedule(id=502, processDefinitionCode=9002, processDefinitionName="second"),
            ]},
        )
        self.outcomes = list(outcomes)
        self.update_calls = []

    def update_schedule(self, payload):
        self.update_calls.append(dict(payload))
        return self.outcomes.pop(0)


class BatchScheduleAlertMutationTests(unittest.TestCase):
    def test_partial_failure_is_visible_without_hiding_success(self):
        client = MutationBatchClient([
            (True, {"status": "UPDATED", "rollback_payload": {"schedule_id": 501}}),
            (False, {"status": "FAILED_ROLLED_BACK", "rollback_payload": {"schedule_id": 502}}),
        ])

        ok, result = client.batch_update_schedule_alerts({
            "project_names": ["DW_DWB"],
            "workflow_release_state": "ONLINE",
            "schedule_release_state": "ONLINE",
            "warning_type": "FAILURE",
            "warning_group_name": "n8n告警触发器",
            "dry_run": False,
            "retry_attempts": 2,
            "retry_delay_ms": 0,
            "rate_limit_ms": 0,
        })

        self.assertFalse(ok)
        self.assertEqual(["UPDATED", "FAILED_ROLLED_BACK"], [item["status"] for item in result["results"]])
        self.assertEqual(2, len(client.update_calls))
        self.assertEqual(2, client.update_calls[0]["retry_attempts"])
        self.assertEqual({
            "total": 2,
            "matched": 2,
            "updated": 1,
            "skipped": 0,
            "failed": 1,
            "verification_failed": 0,
            "rolled_back": 1,
            "rollback_failed": 0,
        }, result["summary"])


if __name__ == "__main__":
    unittest.main()
