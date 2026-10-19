"""Fill `scene_metadata` for the scenes that were processed before the table existed.

Reads every scene with an image and no metadata row, fetches the properties of its Sentinel-2 L1C
image from Earth Engine by gee_id and stores them. Safe to re-run: only missing rows are fetched,
and a scene that failed is retried next time.

    uv run python scripts/backfill_scene_metadata.py --db state.db

Exit codes: 0 all done, 1 some scenes failed, 2 database not found, 3 Earth Engine unavailable.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Run as a plain script, only this folder is on sys.path: make the project importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import DEFAULT_DB, EarthEngineInitError, _initialize_earth_engine  # noqa: E402
from src.ddbb.connection import DBConnection  # noqa: E402
from src.steps.describe import fetch_properties  # noqa: E402


@dataclass
class BackfillResult:
    saved: int = 0
    failed: list[tuple[date, str, str, str]] = field(default_factory=list)  # date, tile, gee_id, error


def backfill(db: DBConnection, fetch: Callable[[str], dict[str, Any]]) -> BackfillResult:
    """Store the metadata of every scene that lacks it. One scene failing never stops the rest."""
    result = BackfillResult()
    for day, tile, gee_id in db.scenes_without_metadata():
        try:
            db.save_scene_metadata(day, tile, fetch(gee_id), gee_id=gee_id)
        except Exception as error:
            result.failed.append((day, tile, gee_id, f"{type(error).__name__}: {error}"))
        else:
            result.saved += 1
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill scene_metadata from Earth Engine.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite state database (default: state.db)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.db.exists():
        print(f"error: {args.db} does not exist", file=sys.stderr)  # never create an empty database here
        return 2

    db = DBConnection(args.db)  # also adds the scene_metadata table to an older database
    pending = len(db.scenes_without_metadata())
    if pending == 0:
        print("nothing to backfill: every scene with an image already has its metadata")
        return 0

    try:
        _initialize_earth_engine()
    except EarthEngineInitError as error:
        print(f"error: {error}", file=sys.stderr)
        print("If you are not logged in, run `earthengine authenticate` once and try again.", file=sys.stderr)
        return 3

    result = backfill(db, fetch_properties)
    print(f"{result.saved} saved, {len(result.failed)} failed, {pending} pending")
    for day, tile, gee_id, error in result.failed:
        print(f"failed: {day.isoformat()} {tile} {gee_id}: {error}", file=sys.stderr)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
