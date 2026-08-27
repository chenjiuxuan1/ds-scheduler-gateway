import calendar
import json
import os
import tempfile
import time
import unittest

from gateway.access import (
    AccessController,
    DEFAULT_LIMITS,
    ROLE_CLASSES,
    classify_action,
    normalize_policy,
    role_classes,
)


def make_policy(**overrides):
    policy = {
        "version": 1,
        "enforce": True,
        "generatedAt": "2026-08-21T00:00:00Z",
        "defaultRole": "operator",
        "enforceUnknown": True,
        "globalLimits": dict(DEFAULT_LIMITS),
        "tokens": {
            "TOK-ADMIN": {
                "user": "admin",
                "role": "admin",
                "enabled": True,
                "allowedActions": None,
                "deniedActions": [],
                "deleteAllowed": True,
                "limits": {},
            },
            "TOK-OP": {
                "user": "operator1",
                "role": "operator",
                "enabled": True,
                "allowedActions": None,
                "deniedActions": ["delete_task"],
                "deleteAllowed": False,
                "limits": {},
            },
            "TOK-DISABLED": {
                "user": "blocked",
                "role": "operator",
                "enabled": False,
                "allowedActions": None,
                "deniedActions": [],
                "deleteAllowed": False,
                "limits": {},
            },
        },
    }
    policy.update(overrides)
    return policy


class AccessControllerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.policy_path = os.path.join(self.dir.name, "access_policy.json")
        self.state_path = os.path.join(self.dir.name, "access_state.json")
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(), fh, ensure_ascii=False)

    def tearDown(self):
        self.dir.cleanup()

    def controller(self, **kwargs):
        return AccessController(policy_path=self.policy_path, state_path=self.state_path, **kwargs)

    def test_action_classification(self):
        self.assertEqual(classify_action("list_projects"), "read")
        self.assertEqual(classify_action("create_workflow"), "write")
        self.assertEqual(classify_action("trigger_workflow"), "control")
        self.assertEqual(classify_action("delete_task"), "delete")
        self.assertEqual(classify_action("disable_task"), "delete")
        self.assertEqual(classify_action("unknown_action"), "unknown")
        self.assertEqual(role_classes("admin"), ROLE_CLASSES["admin"])

    def test_admin_can_delete(self):
        decision = self.controller().authorize("TOK-ADMIN", "delete_task")
        self.assertTrue(decision["allowed"], decision)

    def test_operator_delete_denied(self):
        decision = self.controller().authorize("TOK-OP", "delete_task")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "ACCESS_ACTION_DENIED")

    def test_disabled_user_denied(self):
        decision = self.controller().authorize("TOK-DISABLED", "list_projects")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "ACCESS_USER_DISABLED")

    def test_unknown_token_readonly(self):
        ok = self.controller().authorize("TOK-UNKNOWN", "list_projects")
        self.assertTrue(ok["allowed"], ok)
        denied = self.controller().authorize("TOK-UNKNOWN", "create_workflow")
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["code"], "ACCESS_CLASS_DENIED")

    def test_unknown_token_with_enforce_unknown_false(self):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(enforceUnknown=False), fh, ensure_ascii=False)
        controller = self.controller()
        decision = controller.authorize("TOK-UNKNOWN", "create_workflow")
        self.assertTrue(decision["allowed"], decision)

    def test_allowed_actions_whitelist(self):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(tokens={
                "TOK-WHITE": {
                    "user": "whitelisted",
                    "role": "operator",
                    "enabled": True,
                    "allowedActions": ["list_projects", "get_workflow"],
                    "deniedActions": [],
                    "deleteAllowed": False,
                    "limits": {},
                }
            }), fh, ensure_ascii=False)
        controller = self.controller()
        self.assertTrue(controller.authorize("TOK-WHITE", "list_projects")["allowed"])
        self.assertFalse(controller.authorize("TOK-WHITE", "create_workflow")["allowed"])

    def test_delete_allowed_flag_opens_delete_class(self):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(tokens={
                "TOK-DEL": {
                    "user": "deluser",
                    "role": "operator",
                    "enabled": True,
                    "allowedActions": None,
                    "deniedActions": [],
                    "deleteAllowed": True,
                    "limits": {},
                }
            }), fh, ensure_ascii=False)
        controller = self.controller()
        self.assertTrue(controller.authorize("TOK-DEL", "delete_task")["allowed"])

    def test_limits_block_creates(self):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(globalLimits={**DEFAULT_LIMITS, "maxCreatesPerHour": 2}), fh, ensure_ascii=False)
        controller = self.controller()
        # Two creates allowed, third blocked.
        self.assertTrue(controller.authorize("TOK-ADMIN", "create_workflow")["allowed"])
        self.assertTrue(controller.authorize("TOK-ADMIN", "create_schedule")["allowed"])
        decision = controller.authorize("TOK-ADMIN", "create_workflow")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "ACCESS_LIMIT_EXCEEDED")
        self.assertEqual(decision["detail"]["limitKey"], "maxCreatesPerHour")
        self.assertEqual(decision["detail"]["limit"], 2)
        # Read action still allowed after quota hit.
        self.assertTrue(controller.authorize("TOK-ADMIN", "list_projects")["allowed"])

    def test_limits_roll_with_new_hour_window(self):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(globalLimits={**DEFAULT_LIMITS, "maxCreatesPerHour": 1}), fh, ensure_ascii=False)
        now = 1760000000.0  # fixed timestamp
        controller = self.controller(now=now)
        self.assertTrue(controller.authorize("TOK-ADMIN", "create_workflow")["allowed"])
        self.assertFalse(controller.authorize("TOK-ADMIN", "create_workflow")["allowed"])
        # Next hour window resets the hourly quota.
        controller2 = self.controller(now=now + 3600)
        self.assertTrue(controller2.authorize("TOK-ADMIN", "create_workflow")["allowed"])

    def test_disabled_enforcement_allows_all(self):
        controller = AccessController(
            policy_path=self.policy_path, state_path=self.state_path, disabled=True
        )
        self.assertTrue(controller.authorize("TOK-DISABLED", "delete_task")["allowed"])

    def test_missing_policy_defaults_to_no_enforcement(self):
        missing = os.path.join(self.dir.name, "nope.json")
        controller = AccessController(policy_path=missing, state_path=self.state_path)
        self.assertFalse(controller.policy["enforce"])
        self.assertTrue(controller.authorize("TOK-X", "delete_task")["allowed"])

    def test_normalize_policy_ignores_bad_entries(self):
        normalized = normalize_policy({
            "enforce": True,
            "tokens": {
                "TOK": {"role": "badrole", "enabled": True, "deleteAllowed": "yes"},
                "": {"role": "admin"},
                "TOK2": "not-a-dict",
            },
        })
        self.assertEqual(normalized["defaultRole"], "operator")
        self.assertIn("TOK", normalized["tokens"])
        self.assertEqual(normalized["tokens"]["TOK"]["role"], "operator")
        self.assertNotIn("", normalized["tokens"])
        self.assertNotIn("TOK2", normalized["tokens"])

    def test_bool_strings_are_coerced_robustly(self):
        # Hand-edited policies may carry "false"/0 instead of booleans; a literal
        # "false" string must NOT be treated as truthy.
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(make_policy(tokens={
                "TOK-STR": {
                    "user": "u1", "role": "operator", "enabled": "false",
                    "allowedActions": None, "deniedActions": [],
                    "deleteAllowed": "false", "limits": {},
                },
                "TOK-INT": {
                    "user": "u2", "role": "operator", "enabled": 1,
                    "allowedActions": None, "deniedActions": [],
                    "deleteAllowed": 1, "limits": {},
                },
            }), fh, ensure_ascii=False)
        controller = self.controller()
        # Disabled by string "false".
        self.assertFalse(controller.authorize("TOK-STR", "list_projects")["allowed"])
        # Enabled by integer 1, delete opened by integer 1 (deleteAllowed).
        self.assertTrue(controller.authorize("TOK-INT", "create_workflow")["allowed"])
        self.assertTrue(controller.authorize("TOK-INT", "delete_task")["allowed"])

    def test_window_keys_use_utc_plus_8(self):
        # 2026-08-20 23:50 UTC -> 2026-08-21 07:50 Beijing.
        ts1 = calendar.timegm(time.strptime("2026-08-20 23:50:00", "%Y-%m-%d %H:%M:%S"))
        # 2026-08-21 00:30 UTC -> 2026-08-21 08:30 Beijing.
        ts2 = calendar.timegm(time.strptime("2026-08-21 00:30:00", "%Y-%m-%d %H:%M:%S"))
        controller = self.controller(now=ts1)
        self.assertEqual(controller._hour_key(ts1), "h_2026-08-21T07")
        self.assertEqual(controller._day_key(ts1), "d_2026-08-21")
        self.assertEqual(controller._hour_key(ts2), "h_2026-08-21T08")
        self.assertEqual(controller._day_key(ts2), "d_2026-08-21")


if __name__ == "__main__":
    unittest.main()
