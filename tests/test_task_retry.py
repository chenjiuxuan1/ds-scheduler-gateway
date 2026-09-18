"""Tests for the task-level retry settings (``failRetryTimes`` / ``failRetryInterval``).

These are siblings of ``taskType``/``timeout`` in the DS task definition, not
keys inside ``taskParams``, so the important properties are:

* the value lands at the task-definition level;
* a retry-only edit changes nothing else in the task (no accidental rewrite of
  the script, datasource, environment or the sibling tasks);
* bad input is refused *before* any write reaches DolphinScheduler.
"""

import json
import unittest

from clients.dolphinscheduler_client import DolphinSchedulerClient
from gateway.models import CountryConfig


PROJECT = "158514956085248"
WORKFLOW = "174599383687393"
OTHER_TASK_CODE = 100002


def config(**kwargs):
    values = {
        "country": "cn",
        "base_url": "http://example.invalid",
        "project_code": PROJECT,
        "environment_code": "-1",
    }
    values.update(kwargs)
    return CountryConfig(**values)


def shell_task(
    name="t1",
    code=100001,
    fail_retry_times=0,
    fail_retry_interval=1,
    environment_code=-1,
    script="echo hello",
):
    """A SHELL task with every field the updater would otherwise default in."""
    return {
        "code": code,
        "version": 3,
        "name": name,
        "taskType": "SHELL",
        "description": "",
        "flag": "YES",
        "taskPriority": "MEDIUM",
        "workerGroup": "default",
        "environmentCode": environment_code,
        "failRetryTimes": fail_retry_times,
        "failRetryInterval": fail_retry_interval,
        "timeoutFlag": "CLOSE",
        "timeoutNotifyStrategy": None,
        "timeout": 0,
        "delayTime": 0,
        "taskParams": {
            "rawScript": script,
            "localParams": [],
            "resourceList": [],
            "dependence": {},
            "conditionResult": {"successNode": [""], "failedNode": [""]},
            "waitStartTimeout": {},
            "switchResult": {},
        },
    }


def workflow_detail(tasks, *, release_state="OFFLINE", schedule_release_state="OFFLINE",
                    global_params="[]"):
    return {
        "code": 0,
        "data": {
            "workflowDefinition": {
                "code": WORKFLOW,
                "name": "dim_feature_dic",
                "description": "desc",
                "releaseState": release_state,
                "scheduleReleaseState": schedule_release_state,
                "tenantCode": "default",
                "executionType": "PARALLEL",
                "timeout": 0,
                "globalParams": global_params,
            },
            "taskDefinitionList": tasks,
            "workflowTaskRelationList": [],
            "locations": [],
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

    def written_tasks(self):
        """The task definitions as they were sent to DS."""
        return json.loads(self.writes()[0]["form"]["taskDefinitionJson"])


def payload(**kwargs):
    base = {"project_code": PROJECT, "workflow_code": WORKFLOW, "task_name": "t1"}
    base.update(kwargs)
    return base


WRITE_OK = (True, {"code": 0, "msg": "success"})


class RetryUpdateTests(unittest.TestCase):
    def test_sets_fail_retry_times(self):
        client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
        ok, result = client.update_task(payload(fail_retry_times=3))

        self.assertTrue(ok, result)
        self.assertEqual("fail_retry_times", ",".join(result["change_summary"]["changed_fields"]))
        self.assertEqual(3, result["fail_retry_times"])
        self.assertEqual(3, self.client_task(client)["failRetryTimes"])
        self.assertEqual(1, len(client.writes()))

    def test_sets_fail_retry_interval_in_minutes(self):
        client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
        ok, result = client.update_task(payload(fail_retry_interval=5))

        self.assertTrue(ok, result)
        self.assertEqual(5, result["fail_retry_interval"])
        self.assertEqual(5, self.client_task(client)["failRetryInterval"])

    def test_accepts_camel_case_aliases(self):
        client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
        ok, result = client.update_task(payload(failRetryTimes=4, failRetryInterval=2))

        self.assertTrue(ok, result)
        self.assertEqual(4, result["fail_retry_times"])
        self.assertEqual(2, result["fail_retry_interval"])

    def test_accepts_numeric_strings(self):
        client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
        ok, result = client.update_task(payload(fail_retry_times="7"))
        self.assertTrue(ok, result)
        self.assertEqual(7, result["fail_retry_times"])

    def test_zero_is_honoured_not_treated_as_absent(self):
        """0 must clear retries; it must not be confused with 'not supplied'."""
        client = FakeClient([(True, workflow_detail([shell_task(fail_retry_times=5)])), WRITE_OK])
        ok, result = client.update_task(payload(fail_retry_times=0))

        self.assertTrue(ok, result)
        self.assertEqual(0, result["fail_retry_times"])
        self.assertEqual(0, self.client_task(client)["failRetryTimes"])

    def test_retry_only_update_leaves_every_other_task_field_alone(self):
        """The whole point: a retry edit must not rewrite anything else."""
        original = shell_task(environment_code=99)
        client = FakeClient([(True, workflow_detail([original])), WRITE_OK])
        ok, _result = client.update_task(payload(fail_retry_times=2))
        self.assertTrue(ok)

        written = self.client_task(client)
        expected = json.loads(json.dumps(original))
        expected["failRetryTimes"] = 2
        self.assertEqual(expected, written)

    def test_sibling_tasks_are_untouched(self):
        first = shell_task()
        second = shell_task(name="t2", code=OTHER_TASK_CODE, fail_retry_times=9)
        client = FakeClient([(True, workflow_detail([first, second])), WRITE_OK])
        ok, _result = client.update_task(payload(task_name="t1", fail_retry_times=4))
        self.assertTrue(ok)

        written = client.written_tasks()
        self.assertEqual(4, written[0]["failRetryTimes"])
        self.assertEqual(9, written[1]["failRetryTimes"])
        self.assertEqual(json.loads(json.dumps(second)), written[1])

    def test_targeting_by_task_code(self):
        client = FakeClient([
            (True, workflow_detail([shell_task(), shell_task(name="t2", code=OTHER_TASK_CODE)])),
            WRITE_OK,
        ])
        ok, result = client.update_task(
            {"project_code": PROJECT, "workflow_code": WORKFLOW,
             "task_code": OTHER_TASK_CODE, "fail_retry_times": 6}
        )
        self.assertTrue(ok, result)
        written = client.written_tasks()
        self.assertEqual(0, written[0]["failRetryTimes"])
        self.assertEqual(6, written[1]["failRetryTimes"])

    def test_shell_and_sql_wrappers_inherit_the_capability(self):
        for action in ("update_shell_task", "update_sql_task"):
            with self.subTest(action=action):
                client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
                ok, result = getattr(client, action)(payload(fail_retry_times=8))
                self.assertTrue(ok, result)
                self.assertEqual(8, self.client_task(client)["failRetryTimes"])

    def test_no_op_when_value_already_matches(self):
        client = FakeClient([(True, workflow_detail([shell_task(fail_retry_times=3)]))])
        ok, result = client.update_task(payload(fail_retry_times=3))

        self.assertFalse(ok)
        self.assertEqual("nothing to update", result["message"])
        self.assertEqual([], client.writes())

    @staticmethod
    def client_task(client):
        return client.written_tasks()[0]


class RetryValidationTests(unittest.TestCase):
    """Bad input must be refused before anything is written."""

    def assert_rejected(self, value, *, key="fail_retry_times", code="INVALID_INTEGER_FIELD"):
        client = FakeClient([(True, workflow_detail([shell_task()]))])
        ok, result = client.update_task(payload(**{key: value}))
        self.assertFalse(ok, result)
        self.assertEqual(code, result["code"])
        self.assertEqual(key, result["field"])
        self.assertEqual([], client.writes(), "validation must not reach DS")

    def test_rejects_negative(self):
        self.assert_rejected(-1, code="INTEGER_FIELD_OUT_OF_RANGE")
        self.assert_rejected(-1, key="fail_retry_interval", code="INTEGER_FIELD_OUT_OF_RANGE")

    def test_rejects_booleans(self):
        """True is an int in Python; it must not silently become 1."""
        self.assert_rejected(True)
        self.assert_rejected(False)

    def test_rejects_non_integers(self):
        for bad in ("abc", "", None, [], {}, 1.5, "2.5", "3x"):
            with self.subTest(value=bad):
                client = FakeClient([(True, workflow_detail([shell_task()]))])
                ok, result = client.update_task(payload(fail_retry_times=bad))
                if bad in ("", None):
                    # treated as absent -> nothing to update, still no write
                    self.assertFalse(ok)
                else:
                    self.assertFalse(ok, result)
                    self.assertEqual("INVALID_INTEGER_FIELD", result["code"])
                self.assertEqual([], client.writes())

    def test_rejects_out_of_range(self):
        self.assert_rejected(
            DolphinSchedulerClient.MAX_TASK_FAIL_RETRY_TIMES + 1,
            code="INTEGER_FIELD_OUT_OF_RANGE",
        )
        self.assert_rejected(
            DolphinSchedulerClient.MAX_TASK_FAIL_RETRY_INTERVAL + 1,
            key="fail_retry_interval",
            code="INTEGER_FIELD_OUT_OF_RANGE",
        )
        # the documented ceilings themselves are allowed
        client = FakeClient([(True, workflow_detail([shell_task()])), WRITE_OK])
        ok, _ = client.update_task(payload(
            fail_retry_times=DolphinSchedulerClient.MAX_TASK_FAIL_RETRY_TIMES,
            fail_retry_interval=DolphinSchedulerClient.MAX_TASK_FAIL_RETRY_INTERVAL,
        ))
        self.assertTrue(ok)

    def test_invalid_interval_also_blocks_a_valid_times_change(self):
        """One bad field must not let the other one through."""
        client = FakeClient([(True, workflow_detail([shell_task()]))])
        ok, result = client.update_task(payload(fail_retry_times=5, fail_retry_interval=-2))

        self.assertFalse(ok, result)
        self.assertEqual("fail_retry_interval", result["field"])
        self.assertEqual([], client.writes())


class GlobalParamsGuardOverrideTests(unittest.TestCase):
    """The guard protects structural edits; a retry-only edit may opt out.

    DS holds ``failRetryTimes`` as a scalar sibling of ``taskType``, so a retry
    change cannot touch workflow parameters. Some PK workflows nonetheless have
    an empty ``globalParams`` while their scripts reference ``${dt}`` (they were
    built that way -- the definition log never had params for them), and the
    blanket guard blocked a change that provably cannot make them worse.
    """

    def _client_with_referencing_task(self, global_params):
        detail = workflow_detail(
            [shell_task(script="python3 x.py --dt=${dt}")],
            global_params=global_params,
        )
        return FakeClient([(True, detail), WRITE_OK, (True, detail)])

    def test_guard_still_blocks_by_default(self):
        client = self._client_with_referencing_task("[]")
        ok, result = client.update_task(payload(fail_retry_times=3))
        self.assertFalse(ok)
        self.assertIn("global params are empty", result["message"])
        self.assertEqual(["dt"], result["required_workflow_params"])
        # Nothing reached DS.
        self.assertEqual([], client.writes())

    def test_explicit_opt_out_allows_a_retry_only_change(self):
        client = self._client_with_referencing_task("[]")
        ok, result = client.update_task(
            payload(fail_retry_times=3, fail_retry_interval=2,
                    require_global_params=False)
        )
        self.assertTrue(ok, result)
        self.assertEqual(3, result["fail_retry_times"])
        # The override is recorded, never silent.
        self.assertIsNotNone(result["integrity_warning"])
        self.assertIn("global params are empty", result["integrity_warning"]["message"])

    def test_no_warning_when_the_workflow_has_params(self):
        client = self._client_with_referencing_task(
            '[{"prop":"dt","direct":"IN","type":"VARCHAR","value":""}]'
        )
        ok, result = client.update_task(payload(fail_retry_times=3))
        self.assertTrue(ok, result)
        self.assertIsNone(result["integrity_warning"])

    def test_opt_out_is_boolean_validated(self):
        client = self._client_with_referencing_task("[]")
        ok, result = client.update_task(
            payload(fail_retry_times=3, require_global_params="yes")
        )
        self.assertFalse(ok)
        self.assertEqual("INVALID_BOOLEAN_FIELD", result["code"])
        self.assertEqual([], client.writes())


if __name__ == "__main__":
    unittest.main()
