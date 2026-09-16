#!/usr/bin/env python3
"""Run a fresh configured independent reviewer for one staged worker receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from delegation_core import BoundaryError, ConfigError, load_config, run_independent_reviewer


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "delegation.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", choices=("codex", "claude"), required=True)
    parser.add_argument(
        "--workflow-selection",
        choices=("enabled", "declined"),
        required=True,
        help="The controller's already-completed project-working-loop selection for this session.",
    )
    parser.add_argument("--worker-receipt", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        timeout = (
            int(config.policy["default_timeout_seconds"])
            if args.timeout_seconds is None
            else args.timeout_seconds
        )
        if timeout <= 0 or timeout > int(config.policy["max_timeout_seconds"]):
            raise BoundaryError("timeout_seconds is outside the configured bounded range")
        worker_receipt = json.loads(args.worker_receipt.read_text(encoding="utf-8"))
        receipt = run_independent_reviewer(
            config,
            host=args.host,
            worker_receipt=worker_receipt,
            review_root=args.review_root,
            timeout_seconds=timeout,
            workflow_selection=args.workflow_selection,
        )
        review_root = args.review_root.resolve(strict=True)
        receipt_path = (args.receipt or (review_root / "review-receipt.json")).resolve(strict=False)
        try:
            receipt_path.relative_to(review_root)
        except ValueError as exc:
            raise BoundaryError("review receipt path must be inside review-root") from exc
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt["status"] in {"approved", "rejected"} else 3
    except (BoundaryError, ConfigError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
