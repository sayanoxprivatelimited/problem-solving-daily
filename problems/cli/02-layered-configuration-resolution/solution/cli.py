"""CLI: `python -m solution.cli [--config FILE] [--set K=V] [--json] [--db-port 5433]`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from solution.main import (
    DEFAULT_SCHEMA,
    ConfigError,
    apply_settings,
    load_config,
    parse_flags,
    render_explain,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config",
        description="Resolve layered configuration (flag > env > file > default) and show who won.",
        epilog=(
            "Any schema key is also a flag: --db-port 5433, --web-debug, --no-web-debug, "
            "--api-tags one --api-tags two."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="dotenv file to read (default: none; pass config.env for the usual layout)",
    )
    parser.add_argument(
        "--set",
        metavar="NAME=VALUE",
        action="append",
        default=[],
        help="set a key as if it had been given as a flag (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print the resolved values as JSON")
    parser.add_argument("--reveal-secrets", action="store_true", help="print secrets unmasked (applies to --json and the explain table)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args, rest = build_parser().parse_known_args(argv)

    try:
        flags, leftovers = parse_flags(DEFAULT_SCHEMA, rest)
        if leftovers:
            raise ConfigError(
                "command line: unexpected argument(s) " + " ".join(repr(item) for item in leftovers)
            )
        flags.update(apply_settings(args.set))
        resolved = load_config(args.config, schema=DEFAULT_SCHEMA, flags=flags)
    except FileNotFoundError:
        print(f"error: no such config file: {args.config}", file=sys.stderr)
        return 2
    except (ConfigError, OSError, UnicodeDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        payload = resolved.as_dict(reveal_secrets=args.reveal_secrets)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_explain(resolved, reveal_secrets=args.reveal_secrets))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
