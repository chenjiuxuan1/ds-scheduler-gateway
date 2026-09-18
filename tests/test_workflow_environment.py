"""Tests for the workflow environment (``environmentCode``) switch actions.

These cover the anti-wipe guarantees around ``globalParams``: the switch must
round-trip the live workflow definition verbatim so it can never drop a
workflow global param, and it must refuse the write when the read is not
provably faithful.
"""

import json
import unittest

from clients.dolphinscheduler_client import DolphinSchedulerClient
from gateway.access import classify_action
from gateway.models import CountryConfig
from gateway.utils import SUPPORTED_ACTIONS


PROJECT = "158514956085248"
WORKFLOW = "174599383687393"
OTHER_WORKFLOW = "174599383687394"

GLOBAL_PARAMS_DT = '[{"prop":"dt","direct":"IN","type":"VARCHAR","value":""}]'


def config(**kwargs):
    values = {
        "country": "cn",
        "base_url": "http://example.invalid",
        "project_code": PROJECT,
    }
    values.update(kwargs)
    return CountryConfig(**values)


def task(name, code, environment_code=-1, script="echo ${dt}"):
    return {
        "code": code,
        "name": name,
        "taskType": "SHELL",
        "environmentCode": environment_code,
        "taskParams": {"rawScript": script, "localParams": [], "resourceList": []},
    }


def workflow_detail(
    *,
    tasks,
    global_params="[]",
    global_param_list=None,
    release_state="OFFLINE",
    schedule_release_state="OFFLINE",
    schedule_id=None,
    name="dim_feature_dic",
    workflow_code=WORKFLOW,
):
    workflow_meta = {
        "code": workflow_code,
        "name": name,
        "description": "desc",
        "releaseState": release_state,
        "scheduleReleaseState": schedule_release_state,
        "tenantCode": "default",
        "executionType": "PARALLEL",
        "timeout": 0,
        "globalParams": global_params,
    }
    if global_param_list is not None:
        workflow_meta["globalParamList"] = global_param_list
    if schedule_id:
        workflow_meta["scheduleId"] = schedule_id
    return {
        "code": 0,
        "data": {
            "workflowDefinition": workflow_meta,
            "taskDefinitionList": tasks,
            "workflowTaskRelationList": [],
            "locations": [],
        },
    }


def schedule_record(*, environment_code="-1", schedule_id="77", crontab="0 3/5 * * * ? *"):
    return {
        "code": 0,
        "data": {
            "id": schedule_id,
            "processDefinitionCode": WORKFLOW,
            "warningType": "FAILURE",
            "warningGroupId": 12,
            "failureStrategy": "CONTINUE",
            "processInstancePriority": "MEDIUM",
            "workerGroup": "default",
            "tenantCode": "default",
            "environmentCode": environment_code,
            "releaseState": "ONLINE",
            "schedule": {
                "startTime": "2026-01-01 00:00:00",
                "endTime": "2099-12-31 23:59:59",
                "crontab": crontab,
                "timezoneId": "Asia/Shanghai",
            },
        },
    }


class FakeClient(DolphinSchedulerClient):
    def __init__(self, responses, country_config=None):
        super().__init__(country_config or config(), "token")
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, query=None, form=None, json_body=None):
        self.calls.append({"method": method, "path": path, "query": query, "form": form})
        if not self.responses:
            raise AssertionError(f"unexpected extra DS call: {method} {path}")
        return self.responses.pop(0)

    def writes(self):
        return [call for call in self.calls if call["method"] in ("PUT", "POST")]


def ok(**payload):
    base = {
        "project_code": PROJECT,
        "workflow_code": WORKFLOW,
        "environment_code": "123",
    }
    base.update(payload)
    return base


class ActionRegistrationTests(unittest.TestCase):
    def test_actions_are_registered_and_classified_as_write(self):
        for action in ("update_workflow_environment", "batch_update_workflow_environment"):
            self.assertIn(action, SUPPORTED_ACTIONS)
            self.assertEqual("write", classify_action(action))


class GlobalParamsSafetyTests(unittest.TestCase):
    def test_malformed_global_params_is_refused_without_writing(self):
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1)], global_params="[{not json"))])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("GLOBAL_PARAMS_UNREADABLE", result["code"])
        self.assertEqual([], client.writes())

    def test_inconsistent_global_params_read_is_refused(self):
        client = FakeClient([
            (
                True,
                workflow_detail(
                    tasks=[task("t1", 1)],
                    global_params="[]",
                    global_param_list=[{"prop": "dt", "direct": "IN", "type": "VARCHAR", "value": ""}],
                ),
            )
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("GLOBAL_PARAMS_UNREADABLE", result["code"])
        self.assertEqual(["dt"], result["params_missing_from_writeback"])
        self.assertEqual([], client.writes())

    def test_existing_missing_global_params_is_allowed_with_warning(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)])),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual("UPDATED", result["status"])
        self.assertIn("PRE_EXISTING_MISSING_GLOBAL_PARAMS", result["warnings"])
        self.assertIsNotNone(result["integrity_warning"])
        # The refs the guard complains about are still reported to the caller.
        self.assertEqual(["dt"], result["integrity_warning"]["required_workflow_params"])
        # But nothing was dropped: the empty value is written back verbatim.
        self.assertEqual("[]", client.writes()[0]["form"]["globalParams"])

    def test_require_global_params_blocks_the_switch(self):
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1)]))])
        result_ok, result = client.update_workflow_environment(
            ok(dry_run=False, require_global_params=True)
        )
        self.assertFalse(result_ok)
        self.assertEqual("GLOBAL_PARAMS_REQUIRED", result["code"])
        self.assertEqual([], client.writes())

    def test_global_params_are_round_tripped_verbatim(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], global_params=GLOBAL_PARAMS_DT)),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], global_params=GLOBAL_PARAMS_DT)),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual("UPDATED", result["status"])
        self.assertNotIn("PRE_EXISTING_MISSING_GLOBAL_PARAMS", result["warnings"])
        self.assertIsNone(result["integrity_warning"])
        self.assertEqual(["dt"], result["global_params_preserved"])
        self.assertEqual(GLOBAL_PARAMS_DT, client.writes()[0]["form"]["globalParams"])

    def test_structural_actions_still_refuse_missing_global_params(self):
        """Regression: the new action must not weaken the existing guard."""
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1)]))])
        result_ok, result = client.update_task(
            {"project_code": PROJECT, "workflow_code": WORKFLOW, "task_name": "t1", "script": "echo 1"}
        )
        self.assertFalse(result_ok)
        self.assertIn("global params are empty", result["message"])


class SingleSwitchTests(unittest.TestCase):
    def test_dry_run_is_the_default_and_writes_nothing(self):
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1)]))])
        result_ok, result = client.update_workflow_environment(ok())
        self.assertTrue(result_ok)
        self.assertEqual("DRY_RUN_MATCHED", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["applied"])
        self.assertEqual(1, result["changed_task_count"])
        self.assertEqual([], client.writes())
        self.assertEqual(1, len(client.calls))

    def test_all_tasks_receive_the_new_environment_code(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1), task("t2", 2, environment_code=999)])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123), task("t2", 2, environment_code=123)])),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        written = json.loads(client.writes()[0]["form"]["taskDefinitionJson"])
        self.assertEqual([123, 123], [item["environmentCode"] for item in written])
        self.assertEqual(["t1", "t2"], [item["task_name"] for item in result["changed_tasks"]])

    def test_nothing_to_change_skips_the_write_entirely(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)])),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual("SKIPPED_ALREADY_MATCHED", result["status"])
        self.assertFalse(result["applied"])
        self.assertEqual([], client.writes())

    def test_online_workflow_is_offlined_then_restored(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], release_state="ONLINE")),
            (True, {"code": 0, "msg": "success"}),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], release_state="ONLINE")),
            (True, {"code": 0, "msg": "success"}),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual("UPDATED", result["status"])
        self.assertTrue(result["restored_original_state"])
        release_calls = [call for call in client.calls if call["path"].endswith("/release")]
        self.assertEqual(["OFFLINE", "ONLINE"], [call["query"]["releaseState"] for call in release_calls])

    def test_verification_failure_rolls_back_to_the_original_definition(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 0, "msg": "success"}),
            # Verification still shows the old environment code.
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1)])),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("VERIFICATION_FAILED_ROLLED_BACK", result["status"])
        self.assertTrue(result["rollback"]["verified"])
        rollback_form = client.calls[3]["form"]
        rolled_back_tasks = json.loads(rollback_form["taskDefinitionJson"])
        self.assertEqual([-1], [item["environmentCode"] for item in rolled_back_tasks])
        self.assertEqual("[]", rollback_form["globalParams"])

    def test_failed_rollback_is_reported_loudly(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], global_params=GLOBAL_PARAMS_DT)),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1)], global_params=GLOBAL_PARAMS_DT)),
            (True, {"code": 500, "msg": "rollback rejected"}),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("FAILED_ROLLBACK_FAILED", result["status"])
        self.assertFalse(result["rollback"]["verified"])

    def test_update_rejected_leaves_status_failed_unchanged(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 12345, "msg": "definition is invalid"}),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("FAILED_UNCHANGED", result["status"])
        self.assertEqual("WORKFLOW_UPDATE_FAILED", result["update_error"]["code"])

    def test_payload_validation(self):
        client = FakeClient([])
        result_ok, result = client.update_workflow_environment(ok(dry_run="false"))
        self.assertFalse(result_ok)
        self.assertEqual("INVALID_BOOLEAN_FIELD", result["code"])
        self.assertEqual([], client.calls)

        client = FakeClient([])
        result_ok, result = client.update_workflow_environment(
            {"project_code": PROJECT, "workflow_code": WORKFLOW}
        )
        self.assertFalse(result_ok)
        self.assertEqual("ENVIRONMENT_CODE_REQUIRED", result["code"])

        client = FakeClient([])
        result_ok, result = client.update_workflow_environment(
            {"project_code": PROJECT, "environment_code": "123"}
        )
        self.assertFalse(result_ok)
        self.assertEqual("WORKFLOW_CODE_REQUIRED", result["code"])

    def test_rollback_payload_is_reusable(self):
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1)]))])
        _ok, result = client.update_workflow_environment(ok())
        # Normalized to text so it can be fed straight back as a payload.
        self.assertEqual("-1", result["rollback_payload"]["environment_code"])
        self.assertEqual(["-1"], result["rollback_payload"]["environment_code_before_values"])
        self.assertEqual(False, result["rollback_payload"]["dry_run"])


class ScheduleSwitchTests(unittest.TestCase):
    def test_schedule_environment_is_reported_but_not_switched_by_default(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77")),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], schedule_id="77")),
            (True, schedule_record()),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual("NOT_REQUESTED", result["schedule"]["status"])
        self.assertIn("SCHEDULE_ENVIRONMENT_NOT_SWITCHED", result["warnings"])
        schedule_writes = [
            call for call in client.writes() if call["path"].startswith(f"/projects/{PROJECT}/schedules/")
        ]
        self.assertEqual([], schedule_writes)

    def test_include_schedule_preserves_cron_and_alerts(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77")),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], schedule_id="77")),
            (True, schedule_record()),
            (True, {"code": 0, "msg": "success"}),
            (True, schedule_record(environment_code="123")),
        ])
        result_ok, result = client.update_workflow_environment(
            ok(dry_run=False, include_schedule=True)
        )
        self.assertTrue(result_ok)
        self.assertEqual("UPDATED", result["status"])
        self.assertEqual("UPDATED", result["schedule"]["status"])

        schedule_call = next(
            call for call in client.writes() if call["path"].startswith(f"/projects/{PROJECT}/schedules/")
        )
        form = schedule_call["form"]
        self.assertEqual("123", form["environmentCode"])
        # Every other schedule attribute is carried over untouched.
        self.assertEqual("FAILURE", form["warningType"])
        self.assertEqual(12, form["warningGroupId"])
        self.assertEqual("CONTINUE", form["failureStrategy"])
        self.assertEqual("MEDIUM", form["processInstancePriority"])
        self.assertEqual("default", form["workerGroup"])
        self.assertEqual("ONLINE", form["releaseState"])
        self.assertEqual("0 3/5 * * * ? *", json.loads(form["schedule"])["crontab"])

    def test_unreliable_schedule_snapshot_is_never_written(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77")),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], schedule_id="77")),
            (True, schedule_record(crontab="")),
        ])
        result_ok, result = client.update_workflow_environment(
            ok(dry_run=False, include_schedule=True)
        )
        self.assertTrue(result_ok)
        self.assertEqual("SCHEDULE_SNAPSHOT_UNRELIABLE", result["schedule"]["status"])
        self.assertIn("SCHEDULE_SNAPSHOT_UNRELIABLE", result["warnings"])
        schedule_writes = [
            call for call in client.writes() if call["path"].startswith(f"/projects/{PROJECT}/schedules/")
        ]
        self.assertEqual([], schedule_writes)


class BatchSwitchTests(unittest.TestCase):
    def batch_payload(self, **kwargs):
        payload = {
            "project_code": PROJECT,
            "workflow_codes": [WORKFLOW, OTHER_WORKFLOW],
            "environment_code": "123",
        }
        payload.update(kwargs)
        return payload

    def test_preflight_failure_blocks_every_write(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (
                True,
                workflow_detail(
                    tasks=[task("t2", 2)],
                    global_params="[{broken",
                    workflow_code=OTHER_WORKFLOW,
                ),
            ),
        ])
        result_ok, result = client.batch_update_workflow_environment(self.batch_payload(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("BATCH_PREFLIGHT_FAILED", result["code"])
        self.assertEqual([], client.writes())
        self.assertEqual(2, len(client.calls))

    def test_preflight_enforces_require_global_params_without_writing(self):
        """The hard gate must abort the batch before any write, not mid-way."""
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            self.batch_payload(dry_run=False, require_global_params=True)
        )
        self.assertFalse(result_ok)
        self.assertEqual("BATCH_PREFLIGHT_FAILED", result["code"])
        self.assertEqual("GLOBAL_PARAMS_REQUIRED", result["errors"][0]["code"])
        self.assertEqual([], client.writes())
        self.assertEqual(2, len(client.calls))

    def test_batch_dry_run_default_writes_nothing(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
        ])
        result_ok, result = client.batch_update_workflow_environment(self.batch_payload())
        self.assertTrue(result_ok)
        self.assertTrue(result["dry_run"])
        self.assertEqual(2, result["summary"]["matched"])
        self.assertEqual(0, result["summary"]["updated"])
        self.assertEqual([], client.writes())

    def test_batch_updates_every_workflow(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t2", 2, environment_code=123)], workflow_code=OTHER_WORKFLOW)),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            self.batch_payload(dry_run=False, rate_limit_ms=0)
        )
        self.assertTrue(result_ok)
        self.assertEqual(2, result["summary"]["updated"])
        self.assertEqual(0, result["summary"]["failed"])
        self.assertEqual([WORKFLOW, OTHER_WORKFLOW], [item["workflow_code"] for item in result["results"]])

    def test_batch_rejects_bad_input(self):
        client = FakeClient([])
        result_ok, result = client.batch_update_workflow_environment(
            {"project_code": PROJECT, "environment_code": "123"}
        )
        self.assertFalse(result_ok)
        self.assertEqual("INVALID_WORKFLOW_CODES", result["code"])

        client = FakeClient([])
        result_ok, result = client.batch_update_workflow_environment(
            self.batch_payload(rate_limit_ms=-1)
        )
        self.assertFalse(result_ok)
        self.assertEqual("INVALID_RATE_LIMIT", result["code"])

    def test_batch_deduplicates_workflow_codes(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t1", 1)])),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            {"project_code": PROJECT, "workflow_codes": [WORKFLOW, WORKFLOW], "environment_code": "123"}
        )
        self.assertTrue(result_ok)
        self.assertEqual(1, result["total"])

    def test_batch_continues_after_one_workflow_fails(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            # workflow 1: read then a rejected write
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 500, "msg": "rejected"}),
            # workflow 2: read, accepted write, verification read
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t2", 2, environment_code=123)], workflow_code=OTHER_WORKFLOW)),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            self.batch_payload(dry_run=False, rate_limit_ms=0)
        )
        self.assertTrue(result_ok)
        self.assertEqual(1, result["summary"]["updated"])
        self.assertEqual(1, result["summary"]["failed"])
        statuses = {item["workflow_code"]: item["status"] for item in result["results"]}
        self.assertEqual("FAILED_UNCHANGED", statuses[WORKFLOW])
        self.assertEqual("UPDATED", statuses[OTHER_WORKFLOW])


class HardeningTests(unittest.TestCase):
    def test_non_object_workflow_definition_is_refused(self):
        """A JSON-string workflowDefinition cannot be read reliably; never write."""
        detail = {
            "code": 0,
            "data": {
                "workflowDefinition": '[{"prop":"dt"}]',
                "taskDefinitionList": [task("t1", 1)],
                "workflowTaskRelationList": [],
                "locations": [],
            },
        }
        client = FakeClient([(True, detail)])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertFalse(result_ok)
        self.assertEqual("GLOBAL_PARAMS_UNREADABLE", result["code"])
        self.assertEqual("str", result["workflow_definition_type"])
        self.assertEqual([], client.writes())

    def test_mixed_previous_environments_mark_rollback_unavailable(self):
        client = FakeClient([(True, workflow_detail(tasks=[task("t1", 1, environment_code=1), task("t2", 2, environment_code=-1)]))])
        _ok, result = client.update_workflow_environment(ok())
        self.assertFalse(result["rollback_payload"]["restorable"])
        self.assertIn("did not share a single previous environment code", result["rollback_payload"]["note"])
        self.assertEqual(["1", "-1"], result["rollback_payload"]["environment_code_before_values"])

    def test_partial_change_only_lists_differing_tasks(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[
                task("already", 1, environment_code=123),
                task("needs", 2, environment_code=-1),
            ])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[
                task("already", 1, environment_code=123),
                task("needs", 2, environment_code=123),
            ])),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual(1, result["changed_task_count"])
        self.assertEqual(["needs"], [item["task_name"] for item in result["changed_tasks"]])
        written = json.loads(client.writes()[0]["form"]["taskDefinitionJson"])
        self.assertEqual([123, 123], [item["environmentCode"] for item in written])

    def test_list_form_global_params_round_trip(self):
        params = [{"prop": "dt", "direct": "IN", "type": "VARCHAR", "value": ""}]
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], global_params=params)),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], global_params=params)),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertEqual(["dt"], result["global_params_preserved"])
        self.assertEqual(json.dumps(params, ensure_ascii=False), client.writes()[0]["form"]["globalParams"])

    def test_global_param_map_only_falls_back_and_warns(self):
        detail = {
            "code": 0,
            "data": {
                "workflowDefinition": {
                    "code": WORKFLOW,
                    "name": "wf",
                    "releaseState": "OFFLINE",
                    "scheduleReleaseState": "OFFLINE",
                    "tenantCode": "default",
                    "executionType": "PARALLEL",
                    "timeout": 0,
                    "globalParamMap": {"dt": ""},
                },
                "taskDefinitionList": [task("t1", 1)],
                "workflowTaskRelationList": [],
                "locations": [],
            },
        }
        after = json.loads(json.dumps(detail))
        after["data"]["taskDefinitionList"] = [task("t1", 1, environment_code=123)]
        client = FakeClient([(True, detail), (True, {"code": 0, "msg": "success"}), (True, after)])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertIn("GLOBAL_PARAMS_FROM_MAP_FALLBACK", result["warnings"])
        self.assertEqual(["dt"], result["global_params_preserved"])
        written = json.loads(client.writes()[0]["form"]["globalParams"])
        self.assertEqual("dt", written[0]["prop"])

    def test_online_schedule_is_restored_online(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77", schedule_release_state="ONLINE")),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], schedule_id="77", schedule_release_state="ONLINE")),
            (True, schedule_record()),
            (True, {"code": 0, "msg": "success"}),
        ])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok)
        self.assertTrue(result["restored_original_schedule_state"])
        schedule_release_calls = [
            call for call in client.calls if "/schedules/77/" in call["path"]
        ]
        self.assertEqual(1, len(schedule_release_calls))
        self.assertTrue(schedule_release_calls[0]["path"].endswith("/online"))

    def test_unreadable_global_param_map_is_refused_not_emptied(self):
        """A malformed globalParamMap must never be read as 'no global params'."""
        for bad_map in ("{not json", ["dt"], 42):
            with self.subTest(global_param_map=bad_map):
                detail = {
                    "code": 0,
                    "data": {
                        "workflowDefinition": {
                            "code": WORKFLOW,
                            "name": "wf",
                            "releaseState": "OFFLINE",
                            "scheduleReleaseState": "OFFLINE",
                            "tenantCode": "default",
                            "executionType": "PARALLEL",
                            "timeout": 0,
                            "globalParamMap": bad_map,
                        },
                        "taskDefinitionList": [task("t1", 1)],
                        "workflowTaskRelationList": [],
                        "locations": [],
                    },
                }
                client = FakeClient([(True, detail)])
                result_ok, result = client.update_workflow_environment(ok(dry_run=False))
                self.assertFalse(result_ok)
                self.assertEqual("GLOBAL_PARAMS_UNREADABLE", result["code"])
                self.assertEqual([], client.writes())

    def test_batch_preflight_refuses_unreadable_global_param_map(self):
        detail = {
            "code": 0,
            "data": {
                "workflowDefinition": {
                    "code": WORKFLOW,
                    "name": "wf",
                    "releaseState": "OFFLINE",
                    "scheduleReleaseState": "OFFLINE",
                    "globalParamMap": "{not json",
                },
                "taskDefinitionList": [task("t1", 1)],
                "workflowTaskRelationList": [],
                "locations": [],
            },
        }
        client = FakeClient([(True, detail)])
        result_ok, result = client.batch_update_workflow_environment(
            {"project_code": PROJECT, "workflow_codes": [WORKFLOW], "environment_code": "123", "dry_run": False}
        )
        self.assertFalse(result_ok)
        self.assertEqual("BATCH_PREFLIGHT_FAILED", result["code"])
        self.assertEqual("GLOBAL_PARAMS_UNREADABLE", result["errors"][0]["code"])
        self.assertEqual([], client.writes())

    def test_map_only_extra_names_do_not_fail_verification(self):
        """Map-only names are not written back, so they must not be expected back.

        Regression: expecting them made a successful switch report
        FAILED_ROLLBACK_FAILED and issue a pointless rollback write.
        """
        definition = {
            "code": WORKFLOW,
            "name": "wf",
            "releaseState": "OFFLINE",
            "scheduleReleaseState": "OFFLINE",
            "tenantCode": "default",
            "executionType": "PARALLEL",
            "timeout": 0,
            "globalParams": GLOBAL_PARAMS_DT,
            "globalParamMap": {"dt": "", "bizdate": ""},
        }
        detail = {
            "code": 0,
            "data": {
                "workflowDefinition": definition,
                "taskDefinitionList": [task("t1", 1)],
                "workflowTaskRelationList": [],
                "locations": [],
            },
        }
        # DS rebuilds the map without the map-only name after the write.
        after = json.loads(json.dumps(detail))
        after["data"]["workflowDefinition"]["globalParamMap"] = {"dt": ""}
        after["data"]["taskDefinitionList"] = [task("t1", 1, environment_code=123)]

        client = FakeClient([(True, detail), (True, {"code": 0, "msg": "success"}), (True, after)])
        result_ok, result = client.update_workflow_environment(ok(dry_run=False))
        self.assertTrue(result_ok, result)
        self.assertEqual("UPDATED", result["status"])
        self.assertTrue(result["verification"]["verified"])
        self.assertIn("GLOBAL_PARAMS_MAP_EXTRA_NAMES", result["warnings"])
        self.assertEqual(["dt"], result["global_params_preserved"])
        self.assertEqual(["bizdate"], result["global_params_map_only"])
        # Exactly one write: no spurious rollback PUT.
        self.assertEqual(1, len(client.writes()))

    def test_absent_global_params_field_warns(self):
        detail = {
            "code": 0,
            "data": {
                "workflowDefinition": {
                    "code": WORKFLOW,
                    "name": "wf",
                    "releaseState": "OFFLINE",
                    "scheduleReleaseState": "OFFLINE",
                    "tenantCode": "default",
                    "executionType": "PARALLEL",
                    "timeout": 0,
                },
                "taskDefinitionList": [task("t1", 1, script="echo hi")],
                "workflowTaskRelationList": [],
                "locations": [],
            },
        }
        client = FakeClient([(True, detail)])
        _ok, result = client.update_workflow_environment(ok())
        self.assertIn("GLOBAL_PARAMS_ABSENT_ASSUMED_EMPTY", result["warnings"])
        self.assertEqual("default_empty", result["global_params_source"])

    def test_task_without_environment_code_blocks_single_value_rollback(self):
        client = FakeClient([
            (True, workflow_detail(tasks=[
                {
                    "code": 1,
                    "name": "no_env",
                    "taskType": "SHELL",
                    "taskParams": {"rawScript": "echo ${dt}", "localParams": []},
                },
                task("with_env", 2, environment_code=7),
            ])),
        ])
        _ok, result = client.update_workflow_environment(ok())
        self.assertFalse(result["rollback_payload"]["restorable"])
        self.assertEqual("", result["rollback_payload"]["environment_code"])

    def test_batch_summary_counts_schedule_failures(self):
        """A green workflow with a failed schedule switch must not look clean."""
        client = FakeClient([
            # preflight
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77", schedule_release_state="ONLINE")),
            # execution: read, workflow PUT ok, verify ok
            (True, workflow_detail(tasks=[task("t1", 1)], schedule_id="77", schedule_release_state="ONLINE")),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)], schedule_id="77", schedule_release_state="ONLINE")),
            # schedule switch: read, PUT ok, verify read still shows the old value
            (True, schedule_record(environment_code=-1)),
            (True, {"code": 0, "msg": "success"}),
            (True, schedule_record(environment_code=-1)),
            # rollback PUT then rollback verify
            (True, {"code": 0, "msg": "success"}),
            (True, schedule_record(environment_code=-1)),
            # restore schedule ONLINE
            (True, {"code": 0, "msg": "success"}),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            {"project_code": PROJECT, "workflow_codes": [WORKFLOW], "environment_code": "123",
             "dry_run": False, "include_schedule": True, "rate_limit_ms": 0}
        )
        self.assertTrue(result_ok, result)
        self.assertEqual(1, result["summary"]["updated"])
        self.assertEqual(1, result["summary"]["schedule_failed"])
        self.assertTrue(result["results"][0]["schedule_failed"])
        self.assertIn("SCHEDULE_ENVIRONMENT_FAILED", result["results"][0]["warnings"])

    def test_batch_isolates_an_item_that_raises(self):
        """One exploding workflow must not discard the summary of the others."""
        class Exploding(FakeClient):
            def update_workflow_environment(self, payload):
                if payload.get("workflow_code") == OTHER_WORKFLOW:
                    raise RuntimeError("boom")
                return super().update_workflow_environment(payload)

        client = Exploding([
            # preflight for both
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, workflow_detail(tasks=[task("t2", 2)], workflow_code=OTHER_WORKFLOW)),
            # workflow 1 switches cleanly
            (True, workflow_detail(tasks=[task("t1", 1)])),
            (True, {"code": 0, "msg": "success"}),
            (True, workflow_detail(tasks=[task("t1", 1, environment_code=123)])),
        ])
        result_ok, result = client.batch_update_workflow_environment(
            {"project_code": PROJECT, "workflow_codes": [WORKFLOW, OTHER_WORKFLOW],
             "environment_code": "123", "dry_run": False, "rate_limit_ms": 0}
        )
        self.assertTrue(result_ok, result)
        self.assertEqual(2, result["summary"]["total"])
        self.assertEqual(1, result["summary"]["updated"])
        self.assertEqual(1, result["summary"]["failed"])
        failed = [item for item in result["results"] if item["workflow_code"] == OTHER_WORKFLOW][0]
        self.assertFalse(failed["success"])
        self.assertEqual("GATEWAY_ERROR", failed["code"])

    def test_malformed_schedule_list_body_does_not_raise(self):
        """A bare-array / non-dict schedules body must degrade, not explode."""
        detail = workflow_detail(tasks=[task("t1", 1)])
        # No scheduleReleaseState/scheduleId, so the list API is consulted.
        del detail["data"]["workflowDefinition"]["scheduleReleaseState"]
        client = FakeClient([
            (True, detail),
            (True, [{"id": "1", "processDefinitionCode": WORKFLOW}]),  # bare array body
        ])
        result_ok, result = client.update_workflow_environment(ok())
        self.assertTrue(result_ok, result)
        self.assertEqual("NO_SCHEDULE", result["schedule"]["status"])


if __name__ == "__main__":
    unittest.main()
