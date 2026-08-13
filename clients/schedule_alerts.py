from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable


WARNING_TYPES = {"NONE", "SUCCESS", "FAILURE", "ALL"}

NON_TARGET_FIELDS = (
    "workflow_code",
    "schedule",
    "failure_strategy",
    "process_instance_priority",
    "worker_group",
    "tenant_code",
    "environment_code",
    "start_params",
    "release_state",
)


def normalize_warning_type(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in WARNING_TYPES:
        raise ValueError(normalized)
    return normalized


def unwrap_schedule_record(result: Any) -> Dict[str, Any]:
    record = result
    if isinstance(record, dict) and isinstance(record.get("data"), dict):
        record = record["data"]
    if not isinstance(record, dict):
        return {}
    return deepcopy(record)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return deepcopy(value)


def _schedule_value(record: Dict[str, Any]) -> Dict[str, Any]:
    schedule = _json_value(record.get("schedule"))
    if isinstance(schedule, list):
        schedule = next((item for item in schedule if isinstance(item, dict)), {})
    if isinstance(schedule, dict) and schedule:
        return schedule
    return {
        "startTime": record.get("startTime") or "",
        "endTime": record.get("endTime") or "",
        "crontab": record.get("crontab") or "",
        "timezoneId": record.get("timezoneId") or record.get("timezone") or "",
    }


def normalize_schedule_record(result: Any) -> Dict[str, Any]:
    record = unwrap_schedule_record(result)
    return {
        "id": record.get("id") or record.get("scheduleId"),
        "workflow_code": record.get("processDefinitionCode") or record.get("workflowDefinitionCode"),
        "workflow_code_key": (
            "processDefinitionCode"
            if record.get("processDefinitionCode") not in (None, "")
            else "workflowDefinitionCode"
        ),
        "workflow_name": str(
            record.get("processDefinitionName") or record.get("workflowDefinitionName") or ""
        ).strip(),
        "warning_type": str(record.get("warningType") or "NONE").strip().upper(),
        "warning_group_id": record.get("warningGroupId") if record.get("warningGroupId") is not None else 0,
        "failure_strategy": record.get("failureStrategy"),
        "process_instance_priority": record.get("processInstancePriority"),
        "worker_group": record.get("workerGroup"),
        "tenant_code": record.get("tenantCode"),
        "environment_code": record.get("environmentCode"),
        "start_params": deepcopy(record.get("startParams")),
        "release_state": str(record.get("releaseState") or record.get("scheduleReleaseState") or "").strip(),
        "schedule": _schedule_value(record),
        "raw": record,
    }


def _form_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_schedule_write_form(
    snapshot: Dict[str, Any],
    *,
    warning_type: Any = None,
    warning_group_id: Any = None,
) -> Dict[str, Any]:
    form = {
        snapshot["workflow_code_key"]: snapshot["workflow_code"],
        "warningType": snapshot["warning_type"] if warning_type is None else normalize_warning_type(warning_type),
        "warningGroupId": snapshot["warning_group_id"] if warning_group_id is None else warning_group_id,
        "failureStrategy": snapshot["failure_strategy"],
        "processInstancePriority": snapshot["process_instance_priority"],
        "workerGroup": snapshot["worker_group"],
        "tenantCode": snapshot["tenant_code"],
        "releaseState": snapshot["release_state"],
        "schedule": _form_json(snapshot["schedule"]),
    }
    if snapshot["environment_code"] not in (None, ""):
        form["environmentCode"] = snapshot["environment_code"]
    if snapshot["start_params"] not in (None, ""):
        form["startParams"] = _form_json(snapshot["start_params"])
    return form


def build_rollback_payload(country: str, project_code: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "country": country,
        "project_code": project_code,
        "workflow_code": snapshot["workflow_code"],
        "schedule_id": snapshot["id"],
        "schedule_json": deepcopy(snapshot["schedule"]),
        "warning_type": snapshot["warning_type"],
        "warning_group_id": snapshot["warning_group_id"],
        "failure_strategy": snapshot["failure_strategy"],
        "process_instance_priority": snapshot["process_instance_priority"],
        "worker_group": snapshot["worker_group"],
        "tenant_code": snapshot["tenant_code"],
        "environment_code": snapshot["environment_code"],
        "release_state": snapshot["release_state"],
        "start_params": deepcopy(snapshot["start_params"]),
    }


def compare_fields(
    expected: Dict[str, Any],
    actual: Dict[str, Any],
    fields: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    mismatches = {}
    for field in fields:
        expected_value = expected.get(field)
        actual_value = actual.get(field)
        if field in {"workflow_code", "warning_group_id"}:
            equal = str(expected_value) == str(actual_value)
        else:
            equal = expected_value == actual_value
        if not equal:
            mismatches[field] = {"expected": expected_value, "actual": actual_value}
    return mismatches


def verify_alert_update(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    warning_type: Any,
    warning_group_id: Any,
) -> Dict[str, Dict[str, Any]]:
    expected = deepcopy(before)
    if warning_type is not None:
        expected["warning_type"] = normalize_warning_type(warning_type)
    if warning_group_id is not None:
        expected["warning_group_id"] = warning_group_id
    return compare_fields(
        expected,
        after,
        (*NON_TARGET_FIELDS, "warning_type", "warning_group_id"),
    )


def verify_restored(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return compare_fields(
        before,
        after,
        (*NON_TARGET_FIELDS, "warning_type", "warning_group_id"),
    )
