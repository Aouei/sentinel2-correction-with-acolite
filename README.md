# sentinel2_correction_with_acolite

Idempotent ETL pipeline that corrects Sentinel-2 images with ACOLITE through Google Earth Engine
and records its state in SQLite. Project definition and design live in the Linear project
"Sentinel-2 Pipepile".

## Usage

```bash
# One-time setup: copy the settings template and fill in your Earthdata credentials
cp settings.example.json settings.json

uv run python main.py \
  --date 2026-09-21 --tile 30SXG \
  --roi roi.json --settings settings.json \
  --acolite-version 20231023.0 --acolite-path data/acolite-20231023.0
```

- `--roi` is a GeoJSON `FeatureCollection` with a single `Polygon`. The exported asset is clipped
  to it, so a different ROI (like a different ACOLITE version or settings) counts as a new correction.
- Only `Rrs_B1`..`Rrs_B7`, `TUR_Nechad2016_665`, `chl_oc3`, `pSDB_green` and `pSDB_red` are exported.
- `--db` is optional (default `state.db`).
- Exit code `0` = `SUCCESS`, `1` = `FAIL`, `2` = invalid input files.
- The first run opens the Earth Engine authentication flow if you are not logged in yet.
- Re-running the same date, tile and settings does nothing: the pipeline is idempotent.

## Running the tests

The project is built with TDD: tests are written first, so a failing test is expected until its
code is implemented. Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (including the dev group with pytest)
uv sync

# Run the whole suite
uv run pytest

# Verbose, stop at the first failure
uv run pytest -v -x

# One test file
uv run pytest tests/test_acolite_config.py
uv run pytest tests/test_db_connection.py
uv run pytest tests/test_pipeline.py
uv run pytest tests/test_extract.py
uv run pytest tests/test_transform.py
uv run pytest tests/test_load.py
uv run pytest tests/test_cli.py

# One test by name
uv run pytest tests/test_pipeline.py::test_happy_path_returns_success

# Tests whose name matches an expression
uv run pytest -k "corrected and not error"

# List the tests without running them
uv run pytest --collect-only -q
```

The tests do not need Google Earth Engine, ACOLITE or network access: the pipeline steps are
replaced by fakes and the database is a temporary SQLite file.
