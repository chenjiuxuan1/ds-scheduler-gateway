import unittest

from clients.dolphinscheduler_client import DolphinSchedulerClient
from gateway.models import CountryConfig
from gateway.utils import SUPPORTED_ACTIONS


def config(**kwargs):
    values = {
        "country": "mx",
        "base_url": "http://example.invalid",
        "project_code": "1",
    }
    values.update(kwargs)
    return CountryConfig(**values)


class FakeClient(DolphinSchedulerClient):
    def __init__(self, country_config, responses):
        super().__init__(country_config, "token")
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, query=None, form=None, json_body=None):
        self.calls.append({
            "method": method,
            "path": path,
            "query": query,
            "form": form,
        })
        return self.responses.pop(0)


class InstanceActionTests(unittest.TestCase):
    def test_actions_are_supported(self):
        self.assertIn("stop_instance", SUPPORTED_ACTIONS)
        self.assertIn("force_fail_instance", SUPPORTED_ACTIONS)
        self.assertIn("resolve_project", SUPPORTED_ACTIONS)

    def test_force_fail_defaults_to_unsupported(self):
        client = FakeClient(config(), [])
        ok, result = client.force_fail_instance(
            {"project_code": "1", "instance_id": "2"}
        )
        self.assertFalse(ok)
        self.assertEqual("UNSUPPORTED", result["code"])
        self.assertEqual([], client.calls)

    def test_stop_uses_official_stop_execute_type(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"state": "RUNNING_EXECUTION"}}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": {"state": "STOP"}}),
        ])
        ok, result = client.stop_instance(
            {"project_code": "1", "instance_id": "2", "poll_attempts": 1}
        )
        self.assertTrue(ok)
        execute_call = client.calls[1]
        self.assertEqual("STOP", execute_call["form"]["executeType"])
        self.assertTrue(result["converged"])

    def test_stop_is_idempotent_for_stopped_instance(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"state": "STOP"}}),
        ])
        ok, result = client.stop_instance(
            {"project_code": "1", "instance_id": "2"}
        )
        self.assertTrue(ok)
        self.assertTrue(result["idempotent"])
        self.assertEqual(1, len(client.calls))

    def test_force_fail_uses_only_configured_official_action(self):
        capabilities = {
            "force_fail_instance": {
                "supported": True,
                "execute_type": "FORCE_FAILURE",
            }
        }
        client = FakeClient(config(instance_action_capabilities=capabilities), [
            (True, {"code": 0, "data": {"state": "RUNNING_EXECUTION"}}),
            (True, {"code": 0, "data": True}),
            (True, {"code": 0, "data": {"state": "FAILURE"}}),
        ])
        ok, result = client.force_fail_instance(
            {"project_code": "1", "instance_id": "2", "poll_attempts": 1}
        )
        self.assertTrue(ok)
        self.assertEqual("FORCE_FAILURE", client.calls[1]["form"]["executeType"])
        self.assertTrue(result["converged"])

    def test_failed_workflow_uses_task_log_permission_root_cause(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": [{
                "id": 31,
                "name": "写入策略表",
                "taskCode": 99,
                "state": "FAILURE",
            }]}}),
            (True, {"code": 0, "data": {"log": "INFO - run etl fail\nAccess denied; you need (at least one of) the SELECT privilege(s) on TABLE dm_strategy_ps_mex017_c1c2_s1_month_start for this operation."}}),
        ])
        result = client._find_failed_task_reason("1", {"id": 22})
        self.assertEqual("写入策略表", result["task_name"])
        self.assertEqual(
            "StarRocks 权限不足：缺少表 dm_strategy_ps_mex017_c1c2_s1_month_start 的 SELECT 权限",
            result["reason"],
        )

    def test_task_instances_use_workflow_instance_id_query(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": [{
                "id": 31,
                "workflowInstanceId": 22,
                "name": "failed_task",
                "state": "FAILURE",
            }]}),
        ])
        ok, _result = client.list_task_instances({
            "project_code": "1",
            "instance_id": "22",
            "page_no": 1,
            "page_size": 100,
        })
        self.assertTrue(ok)
        self.assertEqual(
            "/projects/1/task-instances",
            client.calls[0]["path"],
        )
        self.assertEqual("22", client.calls[0]["query"]["workflowInstanceId"])
        self.assertNotIn("processInstanceId", client.calls[0]["query"])


if __name__ == "__main__":
    unittest.main()
