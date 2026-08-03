from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QQ Group Assistant maintenance commands")
    parser.add_argument("app_root", type=Path)
    parser.add_argument("command", choices=("validate", "doctor", "audit-summary"))
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cli = args.app_root.resolve() / "qq_assistant_cli.py"
    if not cli.exists():
        parser.error(f"qq_assistant_cli.py not found under {args.app_root}")
    return subprocess.call([sys.executable, str(cli), args.command, *args.extra], cwd=args.app_root)


if __name__ == "__main__":
    raise SystemExit(main())
