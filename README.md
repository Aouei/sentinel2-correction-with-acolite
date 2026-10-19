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
- Exit code `0` = `SUCCESS`, `1` = `FAIL`, `2` = invalid input files, `3` = Earth Engine unavailable
  (not logged in or offline).
- Log in to Earth Engine once with `uv run earthengine authenticate` before the first run. The CLI
  never opens an interactive login, so an unattended batch cannot hang.
- Re-running the same date, tile and settings does nothing: the pipeline is idempotent.

## Running a whole year

`run_formosa_2018.bat` runs the ETL for every day of 2018 on tile `T29SPA` with `roi.json`:

```bat
run_formosa_2018.bat
```

- Every date runs in its own process, so a failure on one date never stops or affects the others.
- It is safe to re-run: dates that are already corrected finish in seconds without touching Earth
  Engine, and dates that failed are retried.
- Before the loop it checks `uv`, the input files, the ACOLITE folder and the Earth Engine login,
  so a missing prerequisite fails once instead of 365 times.
- One log per date in `logs\T29SPA_<year>\<date>.log`, and `failed.txt` with the dates that failed and
  their exit code. The `.bat` exits with `1` if any date failed.
- Every searched date is recorded in the `scenes` table: `gee_id` NULL means Earth Engine has no image
  that day, and a date with no row at all was never searched. For example, list the empty days with
  `SELECT date FROM scenes WHERE gee_id IS NULL`.
- Optional environment variables: `YEAR` (default `2018`), `LIMIT` (only the first N dates) and
  `DRY_RUN=1` (print the commands without running them).

Rough time: days without an image take a few seconds; each image takes about two minutes.

## Viewing a result in Earth Engine

`gee/psdb_viewer.js` is a Code Editor script that draws one band of a processed scene (`pSDB_green` by
default) with the viridis palette from 0.8 to 1.5 and a color bar. Open
<https://code.earthengine.google.com>, paste the file and press Run. Change `ASSET_ID`, `BAND`, `MIN`
and `MAX` in its CONFIG block to look at another scene or range. It is plain Earth Engine JavaScript,
so it is not part of the Python package and the tests do not run it.

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

## License

This project is licensed under the GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
