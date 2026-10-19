-- Sentinel-2 correction with ACOLITE: state schema (SQLite)
--
-- Idempotency model (decided by Pipeline.run, not by the steps):
--   run  -> returns early, without calling GEE, when `corrected` already has
--           (scene_id, ac_version, params_hash, roi_hash) with status 'corrected'
--   load -> exports to a GEE asset; only marks 'corrected' once the export task is COMPLETED
-- Anything whose status is not 'corrected' is retried on the next run.
--
-- SQLite notes:
--   * Foreign keys are OFF by default: run `PRAGMA foreign_keys = ON;` on every connection.
--   * Dates are stored as ISO-8601 TEXT (YYYY-MM-DD); timestamps as UTC ISO-8601 TEXT.
--   * `IS NOT` is used instead of `IS DISTINCT FROM` so the schema works on SQLite < 3.39.

-- One row per (date, tile) that was searched in GEE. `date` is a plain YYYY-MM-DD date, never a datetime.
--   no row          -> that date and tile were never searched
--   gee_id IS NULL  -> searched, and Earth Engine has no image for it
--   gee_id NOT NULL -> searched, and this is the image that was found
-- (UNIQUE allows many NULLs, so many dates without image can coexist.)
CREATE TABLE IF NOT EXISTS scenes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL CHECK (date(date) IS date),
    tile        TEXT    NOT NULL,
    gee_id      TEXT    UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Sentinel-2 L1C normally has one image per day and tile.
    UNIQUE (date, tile)
);

-- One row per correction attempt target: (scene, ACOLITE version, params, ROI).
-- The export is clipped to the ROI, so the ROI is part of the identity.
-- The UNIQUE constraint is the idempotency key.
CREATE TABLE IF NOT EXISTS corrected (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id     INTEGER NOT NULL REFERENCES scenes (id),
    ac_version   TEXT    NOT NULL,
    params_hash  TEXT    NOT NULL,
    roi_hash     TEXT    NOT NULL,
    status       TEXT    CHECK (status IN ('error', 'corrected')),
    asset_id     TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scene_id, ac_version, params_hash, roi_hash),
    -- A scene can only be 'corrected' when its GEE asset id is known.
    CHECK (status IS NOT 'corrected' OR asset_id IS NOT NULL)
);

-- Append-only error history, one row per failed attempt.
-- scene_id / ac_version / params_hash are nullable because extract can fail
-- before a scene or a correction target exists. date / tile are always known,
-- so every error stays tied to the run that produced it.
CREATE TABLE IF NOT EXISTS errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id     INTEGER REFERENCES scenes (id),
    date         TEXT    NOT NULL CHECK (date(date) IS date),
    tile         TEXT    NOT NULL,
    ac_version   TEXT,
    params_hash  TEXT,
    step         TEXT    NOT NULL CHECK (step IN ('extract', 'transform', 'load')),
    error_msg    TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Python traceback of the failure. NULL when unavailable and for rows written before this
    -- column existed (DBConnection adds it to older databases).
    traceback    TEXT
);

-- One row of metadata of the SOURCE (Sentinel-2 L1C) image of a scene: 1:1 with `scenes`, and only
-- for scenes that have an image. Days without image (gee_id IS NULL) get no row.
-- The typed columns are the ones worth filtering on; `properties` keeps every raw image property
-- (~80) as JSON, queryable with json_extract(properties, '$.NAME') or properties ->> '$.NAME'.
-- Databases created before this table get it from CREATE TABLE IF NOT EXISTS (nothing else changes);
-- rows for scenes processed earlier are filled by scripts/backfill_scene_metadata.py.
-- `spacecraft` is a pattern, not a closed list: a new satellite must not block the ETL.
CREATE TABLE IF NOT EXISTS scene_metadata (
    scene_id                   INTEGER PRIMARY KEY REFERENCES scenes (id),
    product_id                 TEXT    NOT NULL,
    spacecraft                 TEXT    NOT NULL CHECK (spacecraft GLOB 'S2[A-Z]'),
    processing_baseline        TEXT,
    orbit                      INTEGER,
    -- Exact acquisition instant (system:time_start), UTC.
    acquired_at                TEXT    NOT NULL,
    cloudy_pixel_percentage    REAL,
    cloud_coverage_assessment  REAL,
    mean_solar_zenith          REAL,
    mean_solar_azimuth         REAL,
    properties                 TEXT    NOT NULL CHECK (json_valid(properties)),
    created_at                 TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
