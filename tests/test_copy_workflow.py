import json
import unittest

from clients.dolphinscheduler_client import DolphinSchedulerClient
from gateway.models import CountryConfig
from gateway.utils import SUPPORTED_ACTIONS


def config(**kwargs):
    values = {
        "country": "ph",
        "base_url": "http://example.invalid",
        "project_code": "15843450427744",
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


def source_workflow_response():
    return {
        "code": 0,
        "data": {
            "code": "SOURCE_WF_CODE_001",
            "name": "DWD_5M",
            "description": "source workflow",
            "globalParams": [{"prop": "biz_date", "direct": "IN", "type": "VARCHAR", "value": "${system.biz.date}"}],
            "tenantCode": "default",
            "executionType": "PARALLEL",
            "timeout": 0,
            "workflowDefinition": {
                "code": "SOURCE_WF_CODE_001",
                "name": "DWD_5M",
                "description": "source workflow",
                "globalParams": [{"prop": "biz_date", "direct": "IN", "type": "VARCHAR", "value": "${system.biz.date}"}],
                "tenantCode": "default",
                "executionType": "PARALLEL",
                "timeout": 0,
                "taskDefinitionList": [
                    {
                        "code": 100001,
                        "version": 3,
                        "name": "sql_task_1",
                        "taskType": "SQL",
                        "projectCode": 15843450427744,
                        "flag": "YES",
                        "taskPriority": "MEDIUM",
                        "workerGroup": "default",
                        "environmentCode": -1,
                        "failRetryTimes": 0,
                        "failRetryInterval": 1,
                        "timeoutFlag": "CLOSE",
                        "timeout": 0,
                        "delayTime": 0,
                        "taskParams": {
                            "type": "MYSQL",
                            "datasource": 1,
                            "sql": "select 1",
                            "sqlType": "0",
                            "localParams": [],
                            "resourceList": [],
                        },
                    },
                    {
                        "code": 100002,
                        "version": 2,
                        "name": "shell_task_2",
                        "taskType": "SHELL",
                        "projectCode": 15843450427744,
                        "flag": "YES",
                        "taskPriority": "MEDIUM",
                        "workerGroup": "default",
                        "environmentCode": -1,
                        "failRetryTimes": 0,
                        "failRetryInterval": 1,
                        "timeoutFlag": "CLOSE",
                        "timeout": 0,
                        "delayTime": 0,
                        "taskParams": {
                            "rawScript": "echo hello ${biz_date}",
                            "localParams": [],
                            "resourceList": [],
                        },
                    },
                ],
                "workflowTaskRelationList": [
                    {
                        "name": "",
                        "code": 200001,
                        "projectCode": 15843450427744,
                        "processDefinitionCode": "SOURCE_WF_CODE_001",
                        "processDefinitionVersion": 3,
                        "preTaskCode": 100001,
                        "preTaskVersion": 3,
                        "postTaskCode": 100002,
                        "postTaskVersion": 2,
                        "conditionType": 0,
                        "conditionParams": {},
                    },
                ],
                "locations": [
                    {"taskCode": 100001, "x": 100, "y": 100},
                    {"taskCode": 100002, "x": 300, "y": 100},
                ],
            },
        },
    }


class CopyWorkflowTests(unittest.TestCase):
    def test_action_is_supported(self):
        self.assertIn("copy_workflow", SUPPORTED_ACTIONS)

    def test_requires_source_workflow_code(self):
        client = FakeClient(config(), [])
        ok, result = client.copy_workflow(
            {"project_code": "15843450427744", "workflow_name": "DWD_5M_TRIGGER"}
        )
        self.assertFalse(ok)
        self.assertEqual("SOURCE_WORKFLOW_CODE_REQUIRED", result["code"])
        self.assertEqual([], client.calls)

    def test_requires_new_workflow_name(self):
        client = FakeClient(config(), [])
        ok, result = client.copy_workflow(
            {"project_code": "15843450427744", "workflow_code": "SOURCE_WF_CODE_001"}
        )
        self.assertFalse(ok)
        self.assertEqual("WORKFLOW_NAME_REQUIRED", result["code"])
        self.assertEqual([], client.calls)

    def test_copy_preserves_tasks_relations_params_without_schedule(self):
        responses = [
            (True, source_workflow_response()),
            # create workflow success: returns code of new workflow
            (True, {"code": 0, "data": {"code": "NEW_WF_CODE_002"}}),
            # release workflow online
            (True, {"code": 0, "data": True}),
        ]
        client = FakeClient(config(), responses)
        ok, result = client.copy_workflow(
            {
                "project_code": "15843450427744",
                "workflow_code": "SOURCE_WF_CODE_001",
                "workflow_name": "DWD_5M_TRIGGER",
            }
        )
        self.assertTrue(ok, result)
        self.assertEqual("NEW_WF_CODE_002", result["workflow_code"])
        self.assertEqual("DWD_5M_TRIGGER", result["workflow_name"])
        self.assertEqual(2, result["task_definition_count"])
        self.assertEqual(1, result["task_relation_count"])
        self.assertEqual(2, result["location_count"])
        self.assertFalse(result["schedule_created"])
        self.assertTrue(result["trigger_style"])
        self.assertEqual("ONLINE", result["release_state"])
        # global params preserved
        self.assertTrue(result["global_params"])

        # Verify the create form sent.
        create_call = client.calls[1]
        self.assertEqual("POST", create_call["method"])
        self.assertIn("/process-definition", create_call["path"])
        form = create_call["form"]
        self.assertEqual("DWD_5M_TRIGGER", form["name"])
        # no schedule field in a trigger-style copy
        self.assertNotIn("schedule", form)

        task_definitions = json.loads(form["taskDefinitionJson"])
        task_relations = json.loads(form["taskRelationJson"])
        locations = json.loads(form["locations"])

        self.assertEqual(2, len(task_definitions))
        # codes are remapped and differ from the source
        new_codes = [task["code"] for task in task_definitions]
        self.assertNotIn(100001, new_codes)
        self.assertNotIn(100002, new_codes)
        self.assertEqual(len(set(new_codes)), 2)
        # tasks keep their names / types / params
        names = {task["name"] for task in task_definitions}
        self.assertEqual({"sql_task_1", "shell_task_2"}, names)
        # versions reset to 1
        self.assertTrue(all(task["version"] == 1 for task in task_definitions))

        # relations reference the new codes
        rel = task_relations[0]
        self.assertIn(rel["preTaskCode"], set(new_codes))
        self.assertIn(rel["postTaskCode"], set(new_codes))
        self.assertNotEqual(rel["preTaskCode"], rel["postTaskCode"])

        # locations remapped to new codes
        loc_codes = {loc["taskCode"] for loc in locations}
        self.assertTrue(loc_codes.issubset(set(new_codes)))

        # release online call happened
        release_call = client.calls[2]
        self.assertEqual("POST", release_call["method"])
        self.assertIn("release", release_call["path"])
        self.assertEqual("ONLINE", release_call["query"]["releaseState"])

    def test_copy_can_skip_release(self):
        responses = [
            (True, source_workflow_response()),
            (True, {"code": 0, "data": {"code": "NEW_WF_CODE_003"}}),
        ]
        client = FakeClient(config(), responses)
        ok, result = client.copy_workflow(
            {
                "project_code": "15843450427744",
                "workflow_code": "SOURCE_WF_CODE_001",
                "workflow_name": "DWD_5M_TRIGGER",
                "release_workflow": False,
            }
        )
        self.assertTrue(ok, result)
        self.assertEqual("OFFLINE", result["release_state"])
        self.assertFalse(result["release_workflow"])
        # only two calls: get source + create, no release
        self.assertEqual(2, len(client.calls))


if __name__ == "__main__":
    unittest.main()
