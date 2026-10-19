import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import ee

from src.ddbb.connection import DBConnection
from src.pipeline import AcoliteConfig, Pipeline, RunStatus
from src.steps.assets import asset_exists
from src.steps.describe import describe
from src.steps.extract import extract, load_image
from src.steps.load import load
from src.steps.transform import transform

EE_PROJECT = "ee-sergiohercar1"
DEFAULT_DB = Path("state.db")
DATE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")


def iso_date(value: str) -> date:
    """A plain YYYY-MM-DD date; datetimes and other formats are rejected."""
    if not DATE_FORMAT.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a valid date: {value!r}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Correct the Sentinel-2 image of a date and tile with ACOLITE and export it to an Earth Engine asset."
    )
    parser.add_argument("--date", type=iso_date, required=True, help="acquisition date, YYYY-MM-DD")
    parser.add_argument("--tile", required=True, help="Sentinel-2 tile, e.g. 30SXG")
    parser.add_argument("--roi", type=Path, required=True, help="GeoJSON FeatureCollection with one Polygon")
    parser.add_argument("--settings", type=Path, required=True, help="ACOLITE settings, JSON file")
    parser.add_argument("--acolite-version", required=True, help="ACOLITE version, e.g. 20231023.0")
    parser.add_argument("--acolite-path", type=Path, required=True, help="folder that contains the acolite package")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite state database (default: state.db)")
    return parser


def load_settings(path: Path) -> dict[str, Any]:
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError(f"{path}: the settings must be a JSON object")
    return settings


def load_roi(path: Path) -> dict[str, Any]:
    """Geometry of the first (and only) feature of a GeoJSON FeatureCollection."""
    collection = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise ValueError(f"{path}: the ROI must be a GeoJSON FeatureCollection")
    features = collection.get("features") or []
    if not features:
        raise ValueError(f"{path}: the ROI has no features")
    geometry = features[0].get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError(f"{path}: the first feature has no geometry")
    return geometry


class EarthEngineInitError(Exception):
    """Earth Engine could not be initialized (not logged in, offline, wrong project...)."""


def build_pipeline(db_path: Path) -> Pipeline:
    _initialize_earth_engine()
    return Pipeline(
        extract=extract,
        load_image=load_image,
        describe=describe,
        transform=transform,
        load=load,
        asset_exists=asset_exists,
        db=DBConnection(db_path),
    )


def _initialize_earth_engine() -> None:
    # Never authenticate here: an interactive login would hang an unattended batch.
    try:
        ee.Initialize(project=EE_PROJECT)
    except Exception as error:
        raise EarthEngineInitError(f"could not initialize Earth Engine: {error}") from error


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        params = load_settings(args.settings)
        roi = load_roi(args.roi)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    config = AcoliteConfig(version=args.acolite_version, path=args.acolite_path, params=params)
    try:
        pipeline = build_pipeline(args.db)
    except EarthEngineInitError as error:
        print(f"error: {error}", file=sys.stderr)
        print("If you are not logged in, run `earthengine authenticate` once and try again.", file=sys.stderr)
        return 3

    status = pipeline.run(args.date, args.tile, config, roi)
    print(status.value)
    return 0 if status is RunStatus.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())
