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


if __name__ == "__main__":
    unittest.main()
