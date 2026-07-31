import unittest

from tests.test_instance_actions import FakeClient, config


class SearchResourceSqlTests(unittest.TestCase):
    def test_returns_resource_id_from_resource_tree(self):
        client = FakeClient(config(), [
            (True, {
                "code": 0,
                "data": [{
                    "id": 987654,
                    "name": "feature_job.sql",
                    "fullName": "/warehouse/feature_job.sql",
                    "type": "FILE",
                }],
            }),
            (True, {"code": 0, "data": "select apply_id from hive.dwb.feature_source"}),
        ])
        ok, result = client.search_resource_sql({
            "resource_type": "FILE",
            "sql_query": "select apply_id from hive.dwb.feature_source",
            "max_files": 10,
        })
        self.assertTrue(ok)
        self.assertEqual(1, result["matched_file_count"])
        self.assertEqual("987654", result["matched_files"][0]["resource_id"])
        self.assertEqual("/warehouse/feature_job.sql", result["matched_files"][0]["full_name"])


if __name__ == "__main__":
    unittest.main()
