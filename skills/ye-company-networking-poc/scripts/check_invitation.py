#!/usr/bin/env python3
"""Validate an unsent invitation against an observed live composer contract.

The program is intentionally local and side-effect free. It reads one JSON
object from stdin, writes one JSON object to stdout, and never prints message
text in errors.
"""

from __future__ import annotations

import json
import sys
from typing import Any


VALID_BASES = {"counter", "maxlength", "documented_surface"}
VALID_UNITS = {"code_points", "utf16_units", "unknown"}


class InvalidInput(Exception):
    pass


def utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def require_type(value: Any, expected: type, field: str) -> None:
    if not isinstance(value, expected):
        raise InvalidInput(field)


def validate(payload: Any) -> tuple[dict[str, Any], int]:
    require_type(payload, dict, "payload")
    draft = payload.get("draft_text")
    visible = payload.get("visible_text")
    require_type(draft, str, "draft_text")
    require_type(visible, str, "visible_text")

    code_points = len(draft)
    utf16_count = utf16_units(draft)
    issues: list[str] = []

    if draft != visible:
        issues.append("visible_text_mismatch")

    raw_limit = payload.get("live_limit")
    live_limit: dict[str, Any]
    effective_count: int | None = None
    within_limit: bool | None = None

    if raw_limit is None:
        live_limit = {
            "verified": False,
            "basis": None,
            "limit": None,
            "unit": None,
            "observed_used": None,
        }
        issues.append("live_limit_basis_unknown")
    else:
        require_type(raw_limit, dict, "live_limit")
        basis = raw_limit.get("basis")
        limit = raw_limit.get("limit")
        unit = raw_limit.get("unit", "unknown")
        observed_used = raw_limit.get("used")

        if basis not in VALID_BASES:
            raise InvalidInput("live_limit.basis")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise InvalidInput("live_limit.limit")
        if unit not in VALID_UNITS:
            raise InvalidInput("live_limit.unit")
        if observed_used is not None and (
            not isinstance(observed_used, int)
            or isinstance(observed_used, bool)
            or observed_used < 0
        ):
            raise InvalidInput("live_limit.used")

        live_limit = {
            "verified": True,
            "basis": basis,
            "limit": limit,
            "unit": unit,
            "observed_used": observed_used,
        }

        if unit == "code_points":
            effective_count = code_points
        elif unit == "utf16_units":
            effective_count = utf16_count
        elif observed_used is not None:
            effective_count = observed_used
        else:
            live_limit["verified"] = False
            issues.append("live_limit_unit_unknown")

        if observed_used is not None and unit != "unknown":
            if observed_used != effective_count:
                issues.append("observed_count_contradiction")

        if effective_count is not None:
            within_limit = effective_count <= limit
            if not within_limit:
                issues.append("live_limit_exceeded")

    result = {
        "version": 1,
        "counts": {
            "code_points": code_points,
            "utf16_units": utf16_count,
        },
        "visible_text_matches_exactly": draft == visible,
        "live_limit": live_limit,
        "effective_count": effective_count,
        "within_live_limit": within_limit,
        "issues": issues,
    }
    return result, 0 if not issues else 1


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result, exit_code = validate(payload)
    except (InvalidInput, json.JSONDecodeError, UnicodeDecodeError):
        result = {"version": 1, "error": "invalid_input"}
        exit_code = 2
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
