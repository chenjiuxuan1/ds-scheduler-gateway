"""Turn opaque DolphinScheduler HTTP failures into actionable gateway errors.

Before this module the gateway surfaced a bare ``DS_API_ERROR`` whose message
was DS's raw ``{"status": 401, "body": ..., "url": ...}``. A caller could not
tell "my token is wrong" apart from "my token belongs to another country's
instance" apart from "the gateway is broken", and a 401 was repeatedly misread
as a routing or request-format bug. That ambiguity cost real debugging time, so
the mapping lives in one place, with tests.

Rules this module follows:

- it never echoes ``ds_token`` (the token only ever travels in a request header);
- it keeps DS's raw payload under ``detail`` so nothing is hidden from debugging;
- it only translates shapes :meth:`DolphinSchedulerClient.request` actually
  produces, so structured gateway errors pass through untouched.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Tokens live in each DS instance's own database and are not shared between
# countries, so a token minted on one country's DS is simply unknown to every
# other country's DS. That is the single most common cause of a 401 here.
_TOKEN_HELP = (
    "DS 令牌存在各国实例自己的库里、互不相通：在 {country} 以外的国家创建的令牌，"
    "{country} 的 DS 并不认识它。请确认该令牌是在 {country} 的 DolphinScheduler "
    "「安全中心 → 令牌管理 → 新建」创建的，复制时没有多余空格或换行，"
    "并且没有被轮换、停用或删除。"
)

_PERMISSION_HELP = (
    "令牌本身有效，但该账号在 {country} 没有这个动作所需的权限。常见原因："
    "该账号不是目标项目的成员、项目权限不足，或账号只有只读权限而该动作属于写/控制/删除类。"
    "若是写类动作被拦，也可能是网关访问控制把未登记的令牌当作只读"
    "（见本机 config/access_policy.json 的 enforceUnknown）。"
)

_UNREACHABLE_HELP = (
    "网关无法连接 {country} 的 DolphinScheduler（{url}）。"
    "这通常是目标 DS 不可用、网络不通或 base_url 配错，与令牌无关。"
)

REDACTED = "<DS_TOKEN>"


def _redact(value: Any, token: Optional[str]) -> Any:
    """Recursively replace ``token`` with a placeholder.

    The translated error echoes DS's raw body, and a DS error body *could*
    quote the offending token. The gateway must never be the thing that puts a
    credential into a response payload or a log line.
    """
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, REDACTED)
    if isinstance(value, dict):
        return {key: _redact(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    return value


def _detail(
    country: str,
    error_payload: Dict[str, Any],
    status: Optional[int],
    token: Optional[str],
) -> Dict[str, Any]:
    return _redact(
        {
            "country": country,
            "ds_status": status,
            "ds_url": error_payload.get("url"),
            "ds_response": error_payload.get("body"),
        },
        token,
    )


def humanize_ds_error(
    country: str,
    error_payload: Any,
    token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return an actionable replacement for a raw DS transport error.

    ``None`` means "not mine to translate" — the caller should keep whatever
    error it already had. ``token`` is only used to redact it from the output.
    """
    if not isinstance(error_payload, dict):
        return None
    # Structured gateway errors carry a ``code``; raw transport errors do not.
    if "code" in error_payload:
        return None

    status = error_payload.get("status")
    country_label = country or "目标国家"

    if status == 401:
        return {
            "code": "TOKEN_INVALID_OR_WRONG_INSTANCE",
            "message": _redact(
                f"DS 返回 401：令牌无效，或该令牌不属于 {country_label} 实例。"
                + _TOKEN_HELP.format(country=country_label),
                token,
            ),
            "detail": _detail(country, error_payload, 401, token),
        }

    if status == 403:
        return {
            "code": "DS_PERMISSION_DENIED",
            "message": _redact(
                f"DS 返回 403：令牌有效，但账号在 {country_label} 权限不足。"
                + _PERMISSION_HELP.format(country=country_label),
                token,
            ),
            "detail": _detail(country, error_payload, 403, token),
        }

    if status in (502, 503, 504):
        return {
            "code": "DS_UNAVAILABLE",
            "message": _redact(
                f"DS 返回 {status}：{country_label} 的 DolphinScheduler 暂时不可用。"
                + _UNREACHABLE_HELP.format(country=country_label, url=error_payload.get("url")),
                token,
            ),
            "detail": _detail(country, error_payload, status, token),
        }

    # Connection-level failure (no HTTP status at all).
    if status is None and error_payload.get("error"):
        return {
            "code": "DS_UNREACHABLE",
            "message": _redact(
                _UNREACHABLE_HELP.format(country=country_label, url=error_payload.get("url")),
                token,
            ),
            "detail": _detail(country, error_payload, None, token),
        }

    return None
