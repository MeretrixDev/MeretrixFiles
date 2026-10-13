import argparse
import logging
import sys

from app.config import get_settings
from app.services.maintenance import run_cleanup

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("purge", help="delete expired files and old temp files")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    settings.ensure_dirs()

    if args.command == "purge":
        result = run_cleanup(settings)
        if result.skipped:
            print("another cleanup is already running, skipped")
        else:
            print(
                f"expired files removed: {result.files_removed}, "
                f"stale temp files removed: {result.tmp_removed}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())