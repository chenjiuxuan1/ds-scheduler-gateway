"""End-to-end HTTP test for the environment switch action.

Runs the real CLI entry point (``scripts/ds_scheduler_entry.py``) against a
local stub DolphinScheduler server, so this covers argument parsing, base64
payload decoding, country config loading, routing, the DS HTTP form encoding
and the final stdout JSON.
"""

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


def workflow_detail(environment_code):
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
            "taskDefinitionList": [
                {
                    "code": 9001,
                    "name": "shell_task",
                    "taskType": "SHELL",
                    "environmentCode": environment_code,
                    "taskParams": {"rawScript": "echo ${dt}", "localParams": []},
                }
            ],
            "workflowTaskRelationList": [],
            "locations": [],
        },
    }


class StubDSHandler(BaseHTTPRequestHandler):
    requests = []
    current_environment_code = -1

    def log_message(self, *args):  # silence the default stderr logging
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
        self._respond(workflow_detail(type(self).current_environment_code))

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        type(self).requests.append({"method": "PUT", "path": self.path, "form": form})
        tasks = json.loads(form["taskDefinitionJson"])
        type(self).current_environment_code = tasks[0]["environmentCode"]
        self._respond({"code": 0, "msg": "success"})


class EnvironmentSwitchEndToEndTests(unittest.TestCase):
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

    def run_entry(self, action, payload):
        payload_b64 = __import__("base64").b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "ds_scheduler_entry.py"),
                "--country", "testland",
                "--action", action,
                "--ds-token", "token",
                "--request-id", "e2e-001",
                "--payload-b64", payload_b64,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(PROJECT_ROOT),
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def setUp(self):
        StubDSHandler.requests = []
        StubDSHandler.current_environment_code = -1

    def test_dry_run_does_not_touch_dolphinscheduler(self):
        result = self.run_entry(
            "update_workflow_environment",
            {"project_code": PROJECT, "workflow_code": WORKFLOW, "environment_code": "12813621425120"},
        )
        self.assertTrue(result["success"])
        self.assertEqual("DRY_RUN_MATCHED", result["data"]["status"])
        self.assertEqual(
            ["GET"], [item["method"] for item in StubDSHandler.requests]
        )

    def test_real_switch_writes_environment_code_and_keeps_global_params(self):
        result = self.run_entry(
            "update_workflow_environment",
            {
                "project_code": PROJECT,
                "workflow_code": WORKFLOW,
                "environment_code": "12813621425120",
                "dry_run": False,
            },
        )
        self.assertTrue(result["success"], result)
        self.assertEqual("UPDATED", result["data"]["status"])
        self.assertTrue(result["data"]["verification"]["verified"])
        self.assertEqual(
            ["GET", "PUT", "GET"], [item["method"] for item in StubDSHandler.requests]
        )

        put_form = StubDSHandler.requests[1]["form"]
        # In DS 3.4 ``environmentCode`` is a task attribute, so it travels
        # inside taskDefinitionJson rather than as a workflow-level field.
        tasks = json.loads(put_form["taskDefinitionJson"])
        self.assertEqual("12813621425120", str(tasks[0]["environmentCode"]))
        # The live global params were round-tripped verbatim, not rebuilt.
        self.assertEqual(GLOBAL_PARAMS, put_form["globalParams"])

    def test_batch_dry_run_reports_without_writing(self):
        result = self.run_entry(
            "batch_update_workflow_environment",
            {"project_code": PROJECT, "workflow_codes": [WORKFLOW], "environment_code": "123"},
        )
        self.assertTrue(result["success"], result)
        self.assertEqual("BATCH_COMPLETED", result["data"]["status"])
        self.assertEqual(1, result["data"]["summary"]["matched"])
        self.assertEqual(
            ["GET", "GET"], [item["method"] for item in StubDSHandler.requests]
        )


if __name__ == "__main__":
    unittest.main()
