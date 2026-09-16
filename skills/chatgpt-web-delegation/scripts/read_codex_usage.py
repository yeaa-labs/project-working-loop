#!/usr/bin/env python3
"""Read sanitized ChatGPT-backed Codex rate-limit windows via app-server."""

from __future__ import annotations

import json
import select
import subprocess
import sys
import time
from typing import Any


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _bucket(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    used = value.get("usedPercent")
    remaining = None
    if isinstance(used, (int, float)):
        remaining = max(0, min(100, 100 - used))
    return {
        "used_percent": used,
        "remaining_percent": remaining,
        "window_duration_minutes": value.get("windowDurationMins"),
        "resets_at": value.get("resetsAt"),
    }


def main() -> int:
    process = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _send(
            process,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "chatgpt_web_delegation_usage_probe",
                        "title": "ChatGPT Web Delegation Usage Probe",
                        "version": "1.0.0",
                    }
                },
            },
        )
        initialized = False
        deadline = time.monotonic() + 25
        result: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                continue
            line = process.stdout.readline()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            if message.get("id") == 0 and not initialized:
                initialized = True
                _send(process, {"method": "initialized", "params": {}})
                _send(
                    process,
                    {"method": "account/rateLimits/read", "id": 6, "params": {}},
                )
            elif message.get("id") == 6:
                result = message
                break

        if not result or not isinstance(result.get("result"), dict):
            stderr = ""
            if process.poll() is not None and process.stderr is not None:
                stderr = process.stderr.read().strip()
            print(
                json.dumps(
                    {
                        "status": "unavailable",
                        "reason": stderr or "rate-limit response timed out",
                    }
                )
            )
            return 2

        limits = result["result"].get("rateLimits") or {}
        credits = limits.get("credits") if isinstance(limits, dict) else None
        output = {
            "status": "ok",
            "source": "account/rateLimits/read",
            "primary": _bucket(limits.get("primary")),
            "secondary": _bucket(limits.get("secondary")),
            "rate_limit_reached_type": limits.get("rateLimitReachedType"),
            "plan_type": limits.get("planType"),
            "credits_balance": credits.get("balance")
            if isinstance(credits, dict)
            else None,
            "credits_are_tokens": False,
        }
        print(json.dumps(output, sort_keys=True))
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
