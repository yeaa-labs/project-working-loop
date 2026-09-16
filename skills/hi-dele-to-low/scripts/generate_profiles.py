#!/usr/bin/env python3
"""Generate compatibility profiles from the canonical delegation TOML."""

from __future__ import annotations

import argparse
from pathlib import Path

from delegation_core import ConfigError, generate_profile_text, load_config


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "config" / "delegation.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Explicit project agent-profile directory; this tool never guesses an installation target.",
    )
    parser.add_argument("--check", action="store_true", help="Fail if generated files are stale.")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        expected = {
            f"{name}.toml": generate_profile_text(config, name)
            for name in sorted(config.compatibility_profiles)
        }
        if args.check:
            stale = [
                name
                for name, text in expected.items()
                if not (args.output_dir / name).is_file()
                or (args.output_dir / name).read_text(encoding="utf-8") != text
            ]
            if stale:
                print("stale generated profiles: " + ", ".join(stale))
                return 1
            print("generated profiles are current")
            return 0
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in expected.items():
            (args.output_dir / name).write_text(text, encoding="utf-8")
        print("generated " + ", ".join(sorted(expected)))
        return 0
    except (ConfigError, OSError) as exc:
        print(f"generation failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
