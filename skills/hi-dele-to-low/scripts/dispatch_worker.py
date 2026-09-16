#!/usr/bin/env python3
"""Launch one staged unified-delegation worker.

This CLI is intentionally a thin adapter over delegation_core. It has no global
provider mutation and no automatic application of stage output to a source tree.
Its only automatic model changes are the two configured continuation edges, each
of which may fire at most once and always runs in a fresh stage with a fresh
highest controller plan:

* the heavy primary worker stops with a genuine token/quota, usage-window, or
  context capacity failure, so the configured exhaustion fallback runs;
* the configured DeepSeek provider reports a verified HTTP 402 Payment Required
  / Insufficient Balance failure at its configured endpoint, so the configured
  terminal balance fallback (Terra) runs. This applies both to a DeepSeek
  continuation and to an explicitly selected DeepSeek route.

Any other unavailable or failing required path still stops the dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from delegation_core import (
    BoundaryError,
    ConfigError,
    DispatchRequest,
    RoutingInputError,
    load_config,
    run_staged_worker_with_continuation,
)


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "delegation.toml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded Opus, Terra, or DeepSeek worker in an isolated stage."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--route", choices=("terra", "opus", "deepseek"), required=True)
    parser.add_argument(
        "--controller-host",
        choices=("codex", "claude"),
        required=True,
        help="Host that runs the required fresh highest-binding controller plan.",
    )
    parser.add_argument(
        "--workflow-selection",
        choices=("enabled", "declined"),
        required=True,
        help="The controller's already-completed project-working-loop selection for this session.",
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--input", action="append", dest="inputs", required=True)
    parser.add_argument("--approved-output", action="append", dest="outputs", required=True)
    parser.add_argument(
        "--brief-file",
        type=Path,
        required=True,
        help="Explicit non-sensitive task brief; its contents are copied into the stage.",
    )
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument(
        "--lease-dir",
        type=Path,
        help="Shared controller-task ledger for the configured direct-worker cap.",
    )
    parser.add_argument(
        "--dispatch-id",
        help="Safe unique identifier for this direct worker in the shared ledger.",
    )
    parser.add_argument(
        "--continuation-stage-root",
        type=Path,
        help=(
            "Fresh stage for the first configured continuation (capacity "
            "exhaustion, or a verified provider insufficient-balance failure). "
            "Defaults to <stage-root>-continuation-<route>; a later transition "
            "always derives its own fresh sibling stage. Must be new or empty."
        ),
    )
    parser.add_argument(
        "--continuation-dispatch-id",
        help=(
            "Safe unique identifier for the first continuation worker in the "
            "same ledger. A later transition derives its own identifier."
        ),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        help="Optional JSON receipt path. It must be inside --stage-root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare a receipt and sanitized command without starting a child process.",
    )
    return parser


def _receipt_path(stage_root: Path, requested: Path | None) -> Path:
    root = stage_root.resolve(strict=False)
    path = (requested or (root / "receipt.json")).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BoundaryError("receipt path must be inside the stage root") from exc
    return path


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        timeout = (
            int(config.policy["default_timeout_seconds"])
            if args.timeout_seconds is None
            else args.timeout_seconds
        )
        if timeout <= 0 or timeout > int(config.policy["max_timeout_seconds"]):
            raise BoundaryError("timeout_seconds is outside the configured bounded range")
        if args.brief_file.is_symlink() or not args.brief_file.is_file():
            raise BoundaryError("brief-file must be a regular explicit file")
        brief = args.brief_file.read_text(encoding="utf-8")
        request = DispatchRequest(
            route=args.route,
            source_root=args.source_root,
            stage_root=args.stage_root,
            inputs=tuple(args.inputs),
            approved_outputs=tuple(args.outputs),
            brief=brief,
            timeout_seconds=timeout,
            workflow_selection=args.workflow_selection,
            controller_host=args.controller_host,
        )
        outcome = run_staged_worker_with_continuation(
            config,
            request,
            dry_run=args.dry_run,
            ledger_dir=args.lease_dir,
            dispatch_id=args.dispatch_id,
            continuation_stage_root=args.continuation_stage_root,
            continuation_dispatch_id=args.continuation_dispatch_id,
        )
        _write_receipt(_receipt_path(args.stage_root, args.receipt), outcome["primary"])
        for continuation in outcome["continuations"]:
            if continuation["receipt"] is None:
                continue
            # Each continuation receipt is stored in its own fresh stage. Every
            # earlier stage, its failed receipt, and its verified partial
            # outputs are left untouched for inspection.
            _write_receipt(
                _receipt_path(Path(str(continuation["stage_root"])), None),
                continuation["receipt"],
            )
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0 if outcome["status"] in {"planned", "completed"} else 3
    except (BoundaryError, ConfigError, RoutingInputError, OSError) as exc:
        print(json.dumps({"status": "blocked", "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
