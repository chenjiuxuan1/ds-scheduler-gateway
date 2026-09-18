"""End-to-end HTTP test for the task-level retry settings.

Runs the real CLI entry point (``scripts/ds_scheduler_entry.py``) against a
local stub DolphinScheduler server, so this covers argument parsing, base64
payload decoding, country config loading, the DS HTTP form encoding and the
final stdout JSON -- not just the internal builder.

The properties that matter:

* ``failRetryTimes`` / ``failRetryInterval`` land at the **task-definition**
  level (siblings of ``taskType``), never inside ``taskParams``;
* a retry-only edit leaves ``taskParams``, ``environmentCode`` and the workflow
  ``globalParams`` byte-identical;
* bad input is refused with **zero** HTTP requests, so it can never half-write;
* retry and environment changes compose in a single call.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT = "158514956085248"
WORKFLOW = "174599383687393"
GLOBAL_PARAMS = '[{"prop":"dt","direct":"IN","type":"VARCHAR","value":""}]'
TASK_CODE = 9001

TASK = {
    "code": TASK_CODE,
    "version": 7,
    "name": "shell_task",
    "taskType": "SHELL",
    "description": "keep me",
    "flag": "YES",
    "taskPriority": "MEDIUM",
    "workerGroup": "default",
    "environmentCode": 99,
    "failRetryTimes": 0,
    "failRetryInterval": 1,
    "timeoutFlag": "CLOSE",
    "timeoutNotifyStrategy": None,
    "timeout": 0,
    "delayTime": 0,
    "taskParams": {
        "rawScript": "echo ${dt}",
        "localParams": [],
        "resourceList": ["/data/x.sql"],
        "dependence": {},
        "conditionResult": {"successNode": [""], "failedNode": [""]},
        "waitStartTimeout": {},
        "switchResult": {},
    },
}


def workflow_detail(task):
    return {
        "code": 0,
        "data": {
            "workflowDefinition": {
                "code": WORKFLOW,
                "name": "dim_feature_dic",
                "description": "desc",
                "releaseState": "OFFLINE",
                "scheduleReleaseState": "OFFLINE",
                "tenantCode": "default",
                "executionType": "PARALLEL",
                "timeout": 0,
                "globalParams": GLOBAL_PARAMS,
            },
            "taskDefinitionList": [json.loads(json.dumps(task))],
            "workflowTaskRelationList": [],
            "locations": [],
        },
    }


class StubDSHandler(BaseHTTPRequestHandler):
    requests = []
    task = TASK

    def log_message(self, *args):  # silence default stderr logging
        pass

    def _respond(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append({"method": "GET", "path": self.path, "form": None})
        self._respond(workflow_detail(type(self).task))

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        type(self).requests.append({"method": "PUT", "path": self.path, "form": form})
        self._respond({"code": 0, "msg": "success"})


class TaskRetryEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubDSHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

        cls.tmpdir = tempfile.TemporaryDirectory()
        config_path = Path(cls.tmpdir.name) / "countries.json"
        config_path.write_text(
            json.dumps(
                {
                    "testland": {
                        "base_url": f"http://127.0.0.1:{cls.port}/dolphinscheduler",
                        "project_code": PROJECT,
                        "tenant_code": "default",
                        "worker_group": "default",
                        "environment_code": "-1",
                        "api_mode": "auto",
                        "start_endpoint": "auto",
                        "start_code_field": "auto",
                    }
                }
            ),
            encoding="utf-8",
        )
        cls.env = {
            **os.environ,
            "DS_COUNTRIES_CONFIG": str(config_path),
            "DS_ACCESS_DISABLE": "1",
            "PYTHONPATH": str(PROJECT_ROOT),
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmpdir.cleanup()

    def setUp(self):
        StubDSHandler.requests = []
        StubDSHandler.task = TASK

    def serve_task(self, **overrides):
        """Make the stub serve a copy of TASK with the given overrides."""
        task = json.loads(json.dumps(TASK))
        task.update(overrides)
        StubDSHandler.task = task
        return task

    def run_entry(self, action, payload):
        payload_b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "ds_scheduler_entry.py"),
                "--country", "testland",
                "--action", action,
                "--ds-token", "token",
                "--request-id", "e2e-retry",
                "--payload-b64", payload_b64,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(PROJECT_ROOT),
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    # ---- helpers -----------------------------------------------------------

    def writes(self):
        return [r for r in StubDSHandler.requests if r["method"] == "PUT"]

    def written_form(self):
        self.assertEqual(1, len(self.writes()), "expected exactly one PUT")
        return self.writes()[0]["form"]

    def written_task(self):
        return json.loads(self.written_form()["taskDefinitionJson"])[0]

    def retry_payload(self, **extra):
        base = {
            "project_code": PROJECT,
            "workflow_code": WORKFLOW,
            "task_name": "shell_task",
        }
        base.update(extra)
        return base

    # ---- the field lands in the right place --------------------------------

    def test_retry_times_is_written_at_task_definition_level(self):
        result = self.run_entry("update_task", self.retry_payload(fail_retry_times=3))

        self.assertTrue(result["success"], result)
        task = self.written_task()
        # task-definition level, sibling of taskType
        self.assertEqual(3, task["failRetryTimes"])
        # and explicitly NOT buried inside taskParams
        self.assertNotIn("failRetryTimes", task["taskParams"])
        self.assertNotIn("failRetryInterval", task["taskParams"])

    def test_retry_interval_is_written_in_minutes(self):
        result = self.run_entry("update_task", self.retry_payload(fail_retry_interval=17))
        self.assertTrue(result["success"], result)
        self.assertEqual(17, self.written_task()["failRetryInterval"])

    def test_zero_is_written_rather_than_treated_as_absent(self):
        """0 must clear retries on a task that has some."""
        self.serve_task(failRetryTimes=5)
        result = self.run_entry("update_task", self.retry_payload(fail_retry_times=0))
        self.assertTrue(result["success"], result)
        self.assertEqual(0, self.written_task()["failRetryTimes"])

    def test_setting_the_value_it_already_has_writes_nothing(self):
        self.serve_task(failRetryTimes=3)
        result = self.run_entry("update_task", self.retry_payload(fail_retry_times=3))
        self.assertFalse(result["success"], result)
        self.assertEqual("nothing to update", result["error"]["message"])
        self.assertEqual([], self.writes())

    def test_response_echoes_the_resulting_values(self):
        result = self.run_entry(
            "update_task", self.retry_payload(fail_retry_times=4, fail_retry_interval=9)
        )
        self.assertEqual(4, result["data"]["fail_retry_times"])
        self.assertEqual(9, result["data"]["fail_retry_interval"])
        # changed_fields is sorted()ed and de-duplicated by the builder
        self.assertEqual(
            ["fail_retry_interval", "fail_retry_times"],
            result["data"]["change_summary"]["changed_fields"],
        )

    # ---- nothing else is disturbed ----------------------------------------

    def test_retry_only_edit_leaves_everything_else_byte_identical(self):
        result = self.run_entry(
            "update_task",
            self.retry_payload(fail_retry_times=2, fail_retry_interval=3),
        )
        self.assertTrue(result["success"], result)

        written = self.written_task()
        expected = json.loads(json.dumps(TASK))
        expected["failRetryTimes"] = 2
        expected["failRetryInterval"] = 3
        self.assertEqual(expected, written)

    def test_workflow_global_params_and_task_params_survive_a_retry_edit(self):
        """The anti-wipe guarantee must hold for this action too."""
        result = self.run_entry("update_task", self.retry_payload(fail_retry_times=2))
        self.assertTrue(result["success"], result)

        form = self.written_form()
        self.assertEqual(json.loads(GLOBAL_PARAMS), json.loads(form["globalParams"]))

        task = self.written_task()
        # the script that references ${dt} must not be rewritten
        self.assertEqual("echo ${dt}", task["taskParams"]["rawScript"])
        self.assertEqual(["/data/x.sql"], task["taskParams"]["resourceList"])
        self.assertEqual("keep me", task["description"])
        self.assertEqual(99, task["environmentCode"])
        self.assertEqual(7, task["version"])

    def test_retry_and_environment_change_compose(self):
        result = self.run_entry(
            "update_task",
            self.retry_payload(fail_retry_times=6, environment_code="12813621425120"),
        )
        self.assertTrue(result["success"], result)
        task = self.written_task()
        self.assertEqual(6, task["failRetryTimes"])
        self.assertEqual("12813621425120", str(task["environmentCode"]))
        self.assertEqual(
            ["environment_code", "fail_retry_times"],
            result["data"]["change_summary"]["changed_fields"],
        )

    def test_camel_case_alias_is_accepted_end_to_end(self):
        result = self.run_entry(
            "update_task", self.retry_payload(failRetryTimes=5, failRetryInterval=2)
        )
        self.assertTrue(result["success"], result)
        task = self.written_task()
        self.assertEqual(5, task["failRetryTimes"])
        self.assertEqual(2, task["failRetryInterval"])

    # ---- bad input never reaches DolphinScheduler --------------------------

    def assert_no_write(self, action, payload, error_code):
        result = self.run_entry(action, payload)
        self.assertFalse(result["success"], result)
        self.assertEqual(error_code, result["error"]["code"])
        self.assertEqual([], self.writes(), "validation must not write to DS")
        return result

    def test_invalid_types_are_refused_without_any_write(self):
        for bad in ("abc", True, 1.5, "2.5"):
            with self.subTest(value=bad):
                StubDSHandler.requests = []
                self.assert_no_write(
                    "update_task",
                    self.retry_payload(fail_retry_times=bad),
                    "INVALID_INTEGER_FIELD",
                )

    def test_out_of_range_is_refused_without_any_write(self):
        StubDSHandler.requests = []
        self.assert_no_write(
            "update_task",
            self.retry_payload(fail_retry_times=1001),
            "INTEGER_FIELD_OUT_OF_RANGE",
        )
        StubDSHandler.requests = []
        self.assert_no_write(
            "update_task",
            self.retry_payload(fail_retry_interval=10081),
            "INTEGER_FIELD_OUT_OF_RANGE",
        )

    def test_a_bad_retry_value_blocks_a_valid_environment_change_too(self):
        """A valid env switch in the same payload must not be half-applied."""
        StubDSHandler.requests = []
        self.assert_no_write(
            "update_task",
            self.retry_payload(
                environment_code="12813621425120", fail_retry_times="nope"
            ),
            "INVALID_INTEGER_FIELD",
        )

    # ---- the sql/shell wrappers -------------------------------------------

    def test_update_shell_task_carries_the_retry_fields(self):
        result = self.run_entry(
            "update_shell_task",
            self.retry_payload(script="echo hi", fail_retry_times=7),
        )
        self.assertTrue(result["success"], result)
        task = self.written_task()
        self.assertEqual(7, task["failRetryTimes"])
        self.assertEqual("echo hi", task["taskParams"]["rawScript"])


if __name__ == "__main__":
    unittest.main()
