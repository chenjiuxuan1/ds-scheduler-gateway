import unittest

from tests.test_instance_actions import FakeClient, config


class ResolveProjectTests(unittest.TestCase):
    def test_resolves_unique_exact_name(self):
        client = FakeClient(config(), [
            (True, {
                "code": 0,
                "data": {
                    "totalList": [
                        {"code": 123, "name": "营销中台"},
                        {"code": 456, "name": "营销中台测试"},
                    ]
                },
            })
        ])
        ok, result = client.resolve_project({"project_name": "营销中台"})
        self.assertTrue(ok)
        self.assertEqual("123", result["project_code"])

    def test_rejects_ambiguous_exact_name(self):
        client = FakeClient(config(), [
            (True, {
                "code": 0,
                "data": {
                    "totalList": [
                        {"code": 123, "name": "same"},
                        {"code": 456, "name": "same"},
                    ]
                },
            })
        ])
        ok, result = client.resolve_project({"project_name": "same"})
        self.assertFalse(ok)
        self.assertEqual("AMBIGUOUS_PROJECT", result["code"])



class ProjectCodeResolutionTests(unittest.TestCase):
    def test_explicit_project_code_short_circuits(self):
        client = FakeClient(config(), [])
        code, err = client._resolve_project_code({"project_code": "123", "project_name": "other"})
        self.assertEqual("123", code)
        self.assertIsNone(err)
        self.assertEqual(len(client.calls), 0)

    def test_resolves_project_name(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": [{"code": 173384668282560, "name": "haoy_new"}]}}),
        ])
        code, err = client._resolve_project_code({"project_name": "haoy_new"})
        self.assertIsNone(err)
        self.assertEqual("173384668282560", code)

    def test_unresolvable_project_name_errors(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": []}}),
        ])
        code, err = client._resolve_project_code({"project_name": "no_such_project"})
        self.assertIsNone(code)
        self.assertEqual("PROJECT_NOT_FOUND", err.get("code"))

    def test_ambiguous_project_name_errors(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": [
                {"code": 1, "name": "same"},
                {"code": 2, "name": "same"},
            ]}}),
        ])
        code, err = client._resolve_project_code({"project_name": "same"})
        self.assertIsNone(code)
        self.assertEqual("AMBIGUOUS_PROJECT", err.get("code"))

    def test_defaults_to_config_project_when_nothing_specified(self):
        client = FakeClient(config(), [])
        code, err = client._resolve_project_code({})
        self.assertEqual("1", code)
        self.assertIsNone(err)

    def test_list_workflows_uses_explicit_project_code(self):
        client = FakeClient(config(), [(True, {"data": {"totalList": []}})])
        ok, _ = client.list_workflows({"project_code": "123", "search_val": "运营"})
        self.assertTrue(ok)
        self.assertTrue(client.calls[0]["path"].startswith("/projects/123/workflow-definition"))
        self.assertEqual(len(client.calls), 1)

    def test_list_workflows_resolves_project_name(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": [{"code": 173384668282560, "name": "haoy_new"}]}}),
            (True, {"data": {"totalList": [{"name": "运营监控"}]}}),
        ])
        ok, _ = client.list_workflows({"project_name": "haoy_new", "search_val": "运营"})
        self.assertTrue(ok)
        self.assertTrue(client.calls[0]["path"].startswith("/projects"))
        self.assertTrue(client.calls[1]["path"].startswith("/projects/173384668282560/workflow-definition"))

    def test_list_workflows_unresolvable_name_does_not_fall_back_to_default(self):
        client = FakeClient(config(), [
            (True, {"code": 0, "data": {"totalList": []}}),
        ])
        ok, result = client.list_workflows({"project_name": "no_such_project"})
        self.assertFalse(ok)
        self.assertEqual("PROJECT_NOT_FOUND", result.get("code"))
        self.assertEqual(len(client.calls), 1)

    def test_list_workflows_defaults_to_config_project(self):
        client = FakeClient(config(), [(True, {"data": {"totalList": []}})])
        ok, _ = client.list_workflows({"search_val": "运营"})
        self.assertTrue(ok)
        self.assertTrue(client.calls[0]["path"].startswith("/projects/1/workflow-definition"))
if __name__ == "__main__":
    unittest.main()
