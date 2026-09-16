#!/usr/bin/env python3
"""Validate a unified-delegation JSON receipt against its one TOML source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from delegation_core import ConfigError, load_config, validate_receipt


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "delegation.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        errors = validate_receipt(config, receipt)
    except (ConfigError, OSError, json.JSONDecodeError) as exc:
        print(f"invalid_receipt_input: {exc}")
        return 2
    if errors:
        print("\n".join(errors))
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
