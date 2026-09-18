"""Tests for the DS error translation layer.

A bare ``DS_API_ERROR 401`` reads like a gateway or routing bug, but in practice
it is almost always the token (most often: a token minted on another country's
DS instance, because tokens are stored per instance). These tests pin the
translated codes, pin the guidance text, and pin the rule that a token can never
leak into a translated payload.
"""

from __future__ import annotations

import unittest
from unittest import mock

from gateway.diagnostics import REDACTED, humanize_ds_error
from gateway.main import execute_request
from gateway.models import CountryConfig, GatewayRequest

TOKEN = "633c1dd94f755b0c1bcbfdbb0ef9e20a"
DS_URL = "http://10.20.84.176:12345/dolphinscheduler/projects"


def raw_401(body=None, url=DS_URL):
    return {"status": 401, "body": {"raw": ""} if body is None else body, "url": url}


class TranslationTests(unittest.TestCase):
    def test_401_becomes_token_invalid_and_names_the_country(self):
        result = humanize_ds_error("pk", raw_401())
        self.assertEqual("TOKEN_INVALID_OR_WRONG_INSTANCE", result["code"])
        self.assertIn("pk", result["message"])
        self.assertIn("401", result["message"])

    def test_401_explains_that_tokens_are_per_instance(self):
        message = humanize_ds_error("pk", raw_401())["message"]
        # The single most common cause must be stated, not implied.
        self.assertIn("令牌管理", message)
        self.assertIn("安全中心", message)
        self.assertIn("互不相通", message)

    def test_403_is_a_permission_problem_not_an_auth_problem(self):
        result = humanize_ds_error("mx", {"status": 403, "body": {"msg": "no perm"}, "url": DS_URL})
        self.assertEqual("DS_PERMISSION_DENIED", result["code"])
        self.assertIn("权限不足", result["message"])
        # Must not send the caller off to re-mint a working token.
        self.assertNotIn("令牌无效", result["message"])

    def test_503_is_reported_as_unavailable(self):
        result = humanize_ds_error("th", {"status": 503, "body": {"raw": ""}, "url": DS_URL})
        self.assertEqual("DS_UNAVAILABLE", result["code"])
        self.assertIn("503", result["message"])

    def test_connection_failure_has_no_status_and_is_unreachable(self):
        result = humanize_ds_error("ine", {"error": "URLError('timed out')", "url": DS_URL})
        self.assertEqual("DS_UNREACHABLE", result["code"])
        self.assertIn(DS_URL, result["message"])

    def test_detail_preserves_the_raw_ds_payload_for_debugging(self):
        result = humanize_ds_error("pk", raw_401(body={"msg": "unauthorized"}))
        self.assertEqual({"msg": "unauthorized"}, result["detail"]["ds_response"])
        self.assertEqual(DS_URL, result["detail"]["ds_url"])
        self.assertEqual(401, result["detail"]["ds_status"])
        self.assertEqual("pk", result["detail"]["country"])

    def test_structured_gateway_errors_pass_through_untouched(self):
        # Actions raise their own coded errors; those must not be re-labelled.
        structured = {"code": "INVALID_INSTANCE_STATE", "message": "cannot stop"}
        self.assertIsNone(humanize_ds_error("pk", structured))

    def test_untranslated_statuses_pass_through(self):
        self.assertIsNone(humanize_ds_error("pk", {"status": 404, "body": {}, "url": DS_URL}))
        self.assertIsNone(humanize_ds_error("pk", {"status": 500, "body": {}, "url": DS_URL}))

    def test_non_dict_payloads_pass_through(self):
        for payload in (None, "boom", 401, ["a"]):
            self.assertIsNone(humanize_ds_error("pk", payload))

    def test_blank_country_still_produces_readable_text(self):
        result = humanize_ds_error("", raw_401())
        self.assertEqual("TOKEN_INVALID_OR_WRONG_INSTANCE", result["code"])
        self.assertIn("目标国家", result["message"])


class TokenRedactionTests(unittest.TestCase):
    def test_token_quoted_in_the_ds_body_is_redacted(self):
        result = humanize_ds_error(
            "pk",
            raw_401(body={"msg": f"token {TOKEN} not found"}),
            TOKEN,
        )
        self.assertNotIn(TOKEN, str(result))
        self.assertIn(REDACTED, result["detail"]["ds_response"]["msg"])

    def test_token_quoted_in_the_ds_url_is_redacted(self):
        result = humanize_ds_error(
            "pk",
            raw_401(url=f"{DS_URL}?token={TOKEN}"),
            TOKEN,
        )
        self.assertNotIn(TOKEN, str(result))

    def test_redaction_reaches_nested_structures(self):
        result = humanize_ds_error(
            "pk",
            raw_401(body={"outer": {"inner": [f"see {TOKEN}"]}}),
            TOKEN,
        )
        self.assertNotIn(TOKEN, str(result))

    def test_translation_works_without_a_token(self):
        result = humanize_ds_error("pk", raw_401(), None)
        self.assertEqual("TOKEN_INVALID_OR_WRONG_INSTANCE", result["code"])

    def test_redaction_does_not_mangle_unrelated_text(self):
        result = humanize_ds_error("pk", raw_401(), TOKEN)
        self.assertIn("401", result["message"])


class ExecuteRequestIntegrationTests(unittest.TestCase):
    """The translation must survive the real entry point, not just the helper."""

    def _run(self, route_result, action="list_projects", token=TOKEN):
        countries = {
            "pk": CountryConfig(
                country="pk",
                base_url="http://10.20.84.176:12345/dolphinscheduler",
                project_code="169585666733760",
            )
        }
        request = GatewayRequest(
            country="pk",
            action=action,
            ds_token=token,
            request_id="req-1",
            payload={},
        )
        with mock.patch("gateway.main.load_countries_config", return_value=countries), \
             mock.patch("gateway.main.validate_request"), \
             mock.patch("gateway.main.route_request", return_value=(False, route_result)), \
             mock.patch("gateway.main.AccessController") as access:
            access.return_value.authorize.return_value = {"allowed": True}
            return execute_request(request)

    def test_401_from_the_client_surfaces_as_the_actionable_code(self):
        response = self._run(raw_401())
        self.assertFalse(response["success"])
        self.assertEqual("TOKEN_INVALID_OR_WRONG_INSTANCE", response["error"]["code"])
        self.assertIn("pk", response["error"]["message"])

    def test_token_never_appears_in_an_end_to_end_response(self):
        response = self._run(raw_401(body={"msg": f"bad token {TOKEN}"}))
        self.assertNotIn(TOKEN, str(response))

    def test_action_errors_keep_their_own_code(self):
        response = self._run({"code": "PROJECT_CODE_REQUIRED", "message": "project_code is required"})
        self.assertEqual("PROJECT_CODE_REQUIRED", response["error"]["code"])

    def test_untouched_statuses_still_report_ds_api_error(self):
        response = self._run({"status": 500, "body": {"raw": ""}, "url": DS_URL})
        self.assertEqual("DS_API_ERROR", response["error"]["code"])

    def test_debug_payload_is_still_split_out_on_failure(self):
        response = self._run({"status": 401, "body": {"raw": ""}, "url": DS_URL})
        self.assertIsNone(response["data"])

    def test_success_path_is_unchanged(self):
        countries = {
            "pk": CountryConfig(
                country="pk",
                base_url="http://10.20.84.176:12345/dolphinscheduler",
                project_code="169585666733760",
            )
        }
        request = GatewayRequest(
            country="pk",
            action="list_projects",
            ds_token=TOKEN,
            request_id="req-2",
            payload={},
        )
        with mock.patch("gateway.main.load_countries_config", return_value=countries), \
             mock.patch("gateway.main.validate_request"), \
             mock.patch("gateway.main.route_request", return_value=(True, {"totalList": []})), \
             mock.patch("gateway.main.AccessController") as access:
            access.return_value.authorize.return_value = {"allowed": True}
            response = execute_request(request)
        self.assertTrue(response["success"])
        self.assertIsNone(response["error"])
        self.assertEqual({"totalList": []}, response["data"])


if __name__ == "__main__":
    unittest.main()
