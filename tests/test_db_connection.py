import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.ddbb.connection import DBConnection
from src.pipeline import AcoliteConfig
from src.roi import hash_roi

from conftest import DAY, GEE_ID, OTHER_ROI, ROI, TILE

ASSET_ID = "projects/ee-sergiohercar1/assets/ETL/2026-09-21_30SXG_hash_L2W"


def tables(raw_db):
    rows = raw_db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


def corrected_rows(raw_db):
    return raw_db.execute(
        "SELECT ac_version, params_hash, roi_hash, status, asset_id FROM corrected"
    ).fetchall()


def error_rows(raw_db):
    return raw_db.execute(
        "SELECT step, error_msg, date, tile, scene_id, ac_version, params_hash FROM errors"
    ).fetchall()


# --- schema ---------------------------------------------------------------


def test_creates_the_schema_when_the_database_is_new(raw_db):
    assert {"scenes", "corrected", "errors"} <= tables(raw_db)


def test_state_survives_reopening_the_database(db, db_path, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    reopened = DBConnection(db_path)

    assert reopened.is_corrected(DAY, TILE, config, ROI) is True


# --- register_scene -------------------------------------------------------


def test_register_scene_stores_date_tile_and_gee_id(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    assert raw_db.execute("SELECT date, tile, gee_id FROM scenes").fetchall() == [
        ("2026-09-21", TILE, GEE_ID)
    ]


def test_register_scene_is_idempotent(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)
    db.register_scene(DAY, TILE, GEE_ID)

    assert raw_db.execute("SELECT COUNT(*) FROM scenes").fetchone() == (1,)


def test_same_date_in_two_tiles_are_two_scenes(db, raw_db):
    db.register_scene(DAY, "30SXG", GEE_ID)
    db.register_scene(DAY, "30SYG", GEE_ID + "_other")

    assert raw_db.execute("SELECT COUNT(*) FROM scenes").fetchone() == (2,)


# --- is_corrected ---------------------------------------------------------


def test_is_corrected_is_false_for_an_unknown_scene(db, config):
    assert db.is_corrected(DAY, TILE, config, ROI) is False


def test_is_corrected_is_false_for_a_scene_without_correction(db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    assert db.is_corrected(DAY, TILE, config, ROI) is False


def test_is_corrected_does_not_mix_scenes(db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.register_scene(date(2026, 9, 22), TILE, GEE_ID + "_next")
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert db.is_corrected(date(2026, 9, 22), TILE, config, ROI) is False


# --- mark_corrected -------------------------------------------------------


def test_mark_corrected_makes_is_corrected_true(db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert db.is_corrected(DAY, TILE, config, ROI) is True


def test_mark_corrected_stores_version_hash_status_and_asset_id(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert corrected_rows(raw_db) == [(config.version, config.hash(), hash_roi(ROI), "corrected", ASSET_ID)]


def test_is_corrected_depends_on_the_acolite_version(db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    other_version = AcoliteConfig(version="20240101.0", path=config.path, params=config.params)

    assert db.is_corrected(DAY, TILE, other_version, ROI) is False


def test_is_corrected_depends_on_the_params(db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    other_params = AcoliteConfig(
        version=config.version, path=config.path, params={**config.params, "s2_target_res": 20}
    )

    assert db.is_corrected(DAY, TILE, other_params, ROI) is False


def test_is_corrected_ignores_the_acolite_path(db, config, tmp_path):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    moved = AcoliteConfig(version=config.version, path=tmp_path / "elsewhere", params=config.params)

    assert db.is_corrected(DAY, TILE, moved, ROI) is True


def test_two_configs_on_the_same_scene_are_two_corrections(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    other = AcoliteConfig(version="20240101.0", path=config.path, params=config.params)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    db.mark_corrected(DAY, TILE, other, ROI, ASSET_ID + "_2")

    assert len(corrected_rows(raw_db)) == 2


# --- mark_error -----------------------------------------------------------


def test_mark_error_leaves_the_scene_not_corrected(db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_error(DAY, TILE, config, ROI)

    assert db.is_corrected(DAY, TILE, config, ROI) is False


def test_mark_error_stores_the_error_status_without_asset_id(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_error(DAY, TILE, config, ROI)

    assert corrected_rows(raw_db) == [(config.version, config.hash(), hash_roi(ROI), "error", None)]


def test_mark_error_twice_keeps_a_single_row(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_error(DAY, TILE, config, ROI)
    db.mark_error(DAY, TILE, config, ROI)

    assert len(corrected_rows(raw_db)) == 1


def test_a_failed_correction_can_be_corrected_later_on_the_same_row(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_error(DAY, TILE, config, ROI)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert db.is_corrected(DAY, TILE, config, ROI) is True
    assert corrected_rows(raw_db) == [(config.version, config.hash(), hash_roi(ROI), "corrected", ASSET_ID)]


# --- log_error ------------------------------------------------------------


def test_log_error_stores_step_message_date_and_tile(db, raw_db):
    db.log_error("extract", "gee down", DAY, TILE)

    assert error_rows(raw_db) == [("extract", "gee down", "2026-09-21", TILE, None, None, None)]


def test_log_error_does_not_need_the_scene_to_be_registered(db, raw_db):
    db.log_error("extract", "no scene yet", DAY, TILE)

    assert raw_db.execute("SELECT scene_id FROM errors").fetchone() == (None,)


def test_log_error_links_the_scene_when_it_is_registered(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.log_error("transform", "acolite failed", DAY, TILE)

    scene_id = raw_db.execute("SELECT id FROM scenes").fetchone()[0]
    assert raw_db.execute("SELECT scene_id FROM errors").fetchone() == (scene_id,)


def test_log_error_stores_version_and_hash_when_a_config_is_given(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.log_error("load", "export failed", DAY, TILE, config)

    assert error_rows(raw_db) == [
        ("load", "export failed", "2026-09-21", TILE, 1, config.version, config.hash())
    ]


def test_log_error_is_append_only(db, raw_db):
    db.log_error("extract", "first", DAY, TILE)
    db.log_error("extract", "second", DAY, TILE)

    assert [message for (_, message, *_rest) in error_rows(raw_db)] == ["first", "second"]


# --- the ROI is part of the identity of a correction -------------------------


def test_mark_corrected_stores_the_hash_of_the_roi(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert raw_db.execute("SELECT roi_hash FROM corrected").fetchone() == (hash_roi(ROI),)


def test_is_corrected_depends_on_the_roi(db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)

    assert db.is_corrected(DAY, TILE, config, OTHER_ROI) is False


def test_the_same_roi_with_another_key_order_is_the_same_correction(db, config):
    db.register_scene(DAY, TILE, GEE_ID)
    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    reordered = {"coordinates": ROI["coordinates"], "type": ROI["type"]}

    assert db.is_corrected(DAY, TILE, config, reordered) is True


def test_two_rois_on_the_same_scene_and_config_are_two_corrections(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_corrected(DAY, TILE, config, ROI, ASSET_ID)
    db.mark_corrected(DAY, TILE, config, OTHER_ROI, ASSET_ID + "_2")

    assert len(corrected_rows(raw_db)) == 2


def test_mark_error_is_kept_per_roi(db, raw_db, config):
    db.register_scene(DAY, TILE, GEE_ID)

    db.mark_error(DAY, TILE, config, ROI)
    db.mark_error(DAY, TILE, config, OTHER_ROI)

    assert len(corrected_rows(raw_db)) == 2


# --- the traceback column ----------------------------------------------------


def test_log_error_stores_the_traceback(db, raw_db):
    trace = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom"

    db.log_error("transform", "boom", DAY, TILE, traceback=trace)

    assert raw_db.execute("SELECT traceback FROM errors").fetchone() == (trace,)


def test_the_traceback_is_optional(db, raw_db):
    db.log_error("extract", "gee down", DAY, TILE)

    assert raw_db.execute("SELECT traceback FROM errors").fetchone() == (None,)


def test_a_database_created_before_the_traceback_column_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id INTEGER,
            date TEXT NOT NULL,
            tile TEXT NOT NULL,
            ac_version TEXT,
            params_hash TEXT,
            step TEXT NOT NULL,
            error_msg TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        INSERT INTO errors (date, tile, step, error_msg) VALUES ('2018-09-19', 'T29SPA', 'extract', 'old error');
        """
    )
    old.commit()
    old.close()

    DBConnection(path).log_error("load", "new error", DAY, TILE, traceback="trace")

    migrated = sqlite3.connect(path)
    columns = [row[1] for row in migrated.execute("PRAGMA table_info(errors)")]
    rows = migrated.execute("SELECT error_msg, traceback FROM errors ORDER BY id").fetchall()
    migrated.close()
    assert "traceback" in columns
    assert rows == [("old error", None), ("new error", "trace")]


def test_opening_the_database_twice_does_not_fail_on_the_migration(db_path):
    DBConnection(db_path)
    DBConnection(db_path)


# --- scenes without image: gee_id is NULL ---------------------------------------

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"


def gee_id_of(raw_db, day=DAY):
    return raw_db.execute("SELECT gee_id FROM scenes WHERE date = ?", (day.isoformat(),)).fetchone()


def test_register_scene_accepts_a_null_gee_id(db, raw_db):
    db.register_scene(DAY, TILE, None)

    assert raw_db.execute("SELECT date, tile, gee_id FROM scenes").fetchall() == [("2026-09-21", TILE, None)]


def test_several_scenes_without_image_can_coexist(db, raw_db):
    db.register_scene(DAY, TILE, None)
    db.register_scene(date(2026, 9, 22), TILE, None)

    assert raw_db.execute("SELECT COUNT(*) FROM scenes").fetchone() == (2,)


def test_registering_a_null_scene_twice_keeps_a_single_row(db, raw_db):
    db.register_scene(DAY, TILE, None)
    db.register_scene(DAY, TILE, None)

    assert raw_db.execute("SELECT COUNT(*) FROM scenes").fetchone() == (1,)


def test_a_null_gee_id_is_filled_when_the_image_shows_up(db, raw_db):
    db.register_scene(DAY, TILE, None)

    db.register_scene(DAY, TILE, GEE_ID)

    assert gee_id_of(raw_db) == (GEE_ID,)


def test_a_known_gee_id_is_never_replaced_by_null(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.register_scene(DAY, TILE, None)

    assert gee_id_of(raw_db) == (GEE_ID,)


def test_a_known_gee_id_is_never_replaced_by_another_one(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.register_scene(DAY, TILE, GEE_ID + "_other")

    assert gee_id_of(raw_db) == (GEE_ID,)


def test_a_scene_without_image_is_not_corrected(db, config):
    db.register_scene(DAY, TILE, None)

    assert db.is_corrected(DAY, TILE, config, ROI) is False


def test_a_database_with_a_not_null_gee_id_is_migrated_keeping_its_data(tmp_path, config):
    old_schema = SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "gee_id      TEXT    UNIQUE,", "gee_id      TEXT    NOT NULL UNIQUE,"
    )
    assert "NOT NULL UNIQUE" in old_schema, "the schema text changed: update this test"
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(old_schema)
    old.executescript(
        """
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (1, '2018-01-02', 'T29SPA', 'S2/A');
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (7, '2018-09-19', 'T29SPA', 'S2/B');
        INSERT INTO corrected (scene_id, ac_version, params_hash, roi_hash, status, asset_id)
            VALUES (7, '20231023.0', 'p', 'r', 'corrected', 'asset/b');
        INSERT INTO errors (scene_id, date, tile, step, error_msg)
            VALUES (1, '2018-01-02', 'T29SPA', 'load', 'old error');
        """
    )
    old.commit()
    old.close()
    migrated_db = DBConnection(path)

    raw = sqlite3.connect(path)
    notnull = {row[1]: row[3] for row in raw.execute("PRAGMA table_info(scenes)")}
    scenes = raw.execute("SELECT id, date, tile, gee_id FROM scenes ORDER BY id").fetchall()
    linked = raw.execute("SELECT COUNT(*) FROM corrected c JOIN scenes s ON s.id = c.scene_id").fetchone()
    errors = raw.execute("SELECT COUNT(*) FROM errors e JOIN scenes s ON s.id = e.scene_id").fetchone()
    foreign_key_targets = {row[2] for row in raw.execute("PRAGMA foreign_key_list(corrected)")}
    violations = raw.execute("PRAGMA foreign_key_check").fetchall()
    migrated_db.register_scene(date(2018, 1, 5), "T29SPA", None)
    next_scene = raw.execute("SELECT id, gee_id FROM scenes WHERE date = '2018-01-05'").fetchone()
    raw.close()

    assert notnull["gee_id"] == 0
    assert scenes == [(1, "2018-01-02", "T29SPA", "S2/A"), (7, "2018-09-19", "T29SPA", "S2/B")]
    assert linked == (1,)
    assert errors == (1,)
    assert foreign_key_targets == {"scenes"}
    assert violations == []
    assert next_scene == (8, None)  # the AUTOINCREMENT counter kept going


def test_the_migrated_scenes_table_equals_a_new_one(tmp_path):
    old_schema = SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "gee_id      TEXT    UNIQUE,", "gee_id      TEXT    NOT NULL UNIQUE,"
    )
    old_path, new_path = tmp_path / "old.db", tmp_path / "new.db"
    old = sqlite3.connect(old_path)
    old.executescript(old_schema)
    old.close()
    DBConnection(old_path)
    DBConnection(new_path)

    def describe(path):
        connection = sqlite3.connect(path)
        columns = connection.execute("PRAGMA table_info(scenes)").fetchall()
        uniques = sorted(
            tuple(info[2] for info in connection.execute(f"PRAGMA index_info({index[1]})"))
            for index in connection.execute("PRAGMA index_list(scenes)")
        )
        connection.close()
        return columns, uniques

    assert describe(old_path) == describe(new_path)


def test_opening_a_migrated_database_again_does_not_rebuild_it(tmp_path):
    path = tmp_path / "state.db"
    DBConnection(path).register_scene(DAY, TILE, None)

    DBConnection(path)

    raw = sqlite3.connect(path)
    assert raw.execute("SELECT date, gee_id FROM scenes").fetchall() == [("2026-09-21", None)]
    raw.close()


# --- scene_metadata: one row of source-image metadata per scene with image -----------

SOURCE_PROPERTIES = {
    "PRODUCT_ID": "S2B_MSIL1C_20260921T105031_N0511_R051_T30SXG_20260921T130000",
    "SPACECRAFT_NAME": "Sentinel-2B",
    "PROCESSING_BASELINE": "05.11",
    "SENSING_ORBIT_NUMBER": 51,
    "system:time_start": 1789987831024,
    "CLOUDY_PIXEL_PERCENTAGE": 3.5,
    "CLOUD_COVERAGE_ASSESSMENT": 3.25,
    "MEAN_SOLAR_ZENITH_ANGLE": 40.5,
    "MEAN_SOLAR_AZIMUTH_ANGLE": 150.25,
    "NODATA_PIXEL_PERCENTAGE": 0,
}

METADATA_COLUMNS = (
    "product_id, spacecraft, processing_baseline, orbit, acquired_at, "
    "cloudy_pixel_percentage, cloud_coverage_assessment, mean_solar_zenith, mean_solar_azimuth"
)


def test_creates_the_scene_metadata_table(raw_db):
    assert "scene_metadata" in tables(raw_db)


def test_save_scene_metadata_stores_the_typed_columns(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    assert raw_db.execute(f"SELECT {METADATA_COLUMNS} FROM scene_metadata").fetchall() == [
        (
            "S2B_MSIL1C_20260921T105031_N0511_R051_T30SXG_20260921T130000",
            "S2B",
            "05.11",
            51,
            "2026-09-21T10:50:31.024Z",
            3.5,
            3.25,
            40.5,
            150.25,
        )
    ]


def test_save_scene_metadata_links_the_scene_one_to_one(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    scene_id = raw_db.execute("SELECT id FROM scenes").fetchone()[0]
    assert raw_db.execute("SELECT scene_id FROM scene_metadata").fetchall() == [(scene_id,)]


def test_the_raw_properties_are_queryable_json(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    row = raw_db.execute(
        "SELECT properties ->> '$.NODATA_PIXEL_PERCENTAGE', json_extract(properties, '$.PROCESSING_BASELINE') "
        "FROM scene_metadata"
    ).fetchone()
    assert row == (0, "05.11")


def test_save_scene_metadata_is_idempotent(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)

    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)
    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    assert raw_db.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (1,)


def test_save_scene_metadata_refreshes_the_existing_row(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)
    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    db.save_scene_metadata(DAY, TILE, {**SOURCE_PROPERTIES, "PROCESSING_BASELINE": "05.12"}, gee_id=GEE_ID)

    assert raw_db.execute("SELECT processing_baseline FROM scene_metadata").fetchall() == [("05.12",)]


def test_save_scene_metadata_needs_a_registered_scene(db):
    with pytest.raises(LookupError):
        db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)


def test_save_scene_metadata_refuses_an_image_that_is_not_the_registered_one(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)
    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)
    other = {**SOURCE_PROPERTIES, "PRODUCT_ID": "S2A_MSIL1C_other", "PROCESSING_BASELINE": "09.99"}

    with pytest.raises(ValueError, match="other_image"):
        db.save_scene_metadata(DAY, TILE, other, gee_id="COPERNICUS/S2_HARMONIZED/other_image")

    assert raw_db.execute("SELECT product_id, processing_baseline FROM scene_metadata").fetchall() == [
        (SOURCE_PROPERTIES["PRODUCT_ID"], "05.11")
    ]


def test_registered_gee_id_returns_the_id_of_the_registered_image(db):
    db.register_scene(DAY, TILE, GEE_ID)

    assert db.registered_gee_id(DAY, TILE) == GEE_ID


def test_registered_gee_id_is_none_for_a_scene_searched_without_image(db):
    db.register_scene(DAY, TILE, None)

    assert db.registered_gee_id(DAY, TILE) is None


def test_registered_gee_id_is_none_for_an_unregistered_scene_or_another_tile(db):
    assert db.registered_gee_id(DAY, TILE) is None
    db.register_scene(DAY, TILE, GEE_ID)
    assert db.registered_gee_id(DAY, "30SYG") is None


def test_save_scene_metadata_accepts_the_short_and_the_full_gee_id(db, raw_db):
    short_id = GEE_ID.rsplit("/", 1)[-1]
    db.register_scene(DAY, TILE, short_id)  # the database keeps the short system:index

    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)
    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=short_id)

    assert raw_db.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (1,)


def test_save_scene_metadata_refuses_a_scene_registered_without_image(db, raw_db):
    db.register_scene(DAY, TILE, None)

    with pytest.raises(ValueError):
        db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES, gee_id=GEE_ID)

    assert raw_db.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (0,)


def test_a_failed_save_leaves_no_row(db, raw_db):
    db.register_scene(DAY, TILE, GEE_ID)
    incomplete = {key: value for key, value in SOURCE_PROPERTIES.items() if key != "PRODUCT_ID"}

    with pytest.raises(ValueError):
        db.save_scene_metadata(DAY, TILE, incomplete, gee_id=GEE_ID)

    assert raw_db.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (0,)


def test_properties_must_be_valid_json(raw_db, db):
    db.register_scene(DAY, TILE, GEE_ID)

    with pytest.raises(sqlite3.IntegrityError):
        raw_db.execute(
            "INSERT INTO scene_metadata (scene_id, product_id, spacecraft, acquired_at, properties) "
            "VALUES (1, 'S2B_x', 'S2B', '2026-09-21T10:50:31.024Z', 'not json')"
        )


def test_scene_metadata_requires_an_existing_scene(db, db_path):
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO scene_metadata (scene_id, product_id, spacecraft, acquired_at, properties) "
            "VALUES (99, 'S2B_x', 'S2B', '2026-09-21T10:50:31.024Z', '{}')"
        )
    raw.close()


def test_scenes_without_metadata_lists_only_scenes_with_image_and_no_row(db):
    db.register_scene(DAY, TILE, GEE_ID)
    db.register_scene(date(2026, 9, 22), TILE, GEE_ID + "_done")
    db.register_scene(date(2026, 9, 23), TILE, None)
    db.save_scene_metadata(date(2026, 9, 22), TILE, SOURCE_PROPERTIES, gee_id=GEE_ID + "_done")

    assert db.scenes_without_metadata() == [(DAY, TILE, GEE_ID)]


def test_scenes_without_metadata_is_ordered_by_date(db):
    db.register_scene(date(2026, 9, 23), TILE, GEE_ID + "_c")
    db.register_scene(DAY, TILE, GEE_ID + "_a")
    db.register_scene(date(2026, 9, 22), TILE, GEE_ID + "_b")

    assert [day for day, _tile, _gee_id in db.scenes_without_metadata()] == [
        DAY,
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]


def test_a_database_without_scene_metadata_gets_the_table_and_keeps_its_data(tmp_path, config):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_PATH.read_text(encoding="utf-8").split("-- One row of metadata")[0])
    old.executescript(
        """
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (1, '2018-01-02', 'T29SPA', 'S2/A');
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (2, '2018-01-03', 'T29SPA', NULL);
        INSERT INTO corrected (scene_id, ac_version, params_hash, roi_hash, status, asset_id)
            VALUES (1, '20231023.0', 'p', 'r', 'corrected', 'asset/a');
        """
    )
    old.commit()
    assert "scene_metadata" not in {r[0] for r in old.execute("SELECT name FROM sqlite_master")}
    old.close()

    migrated = DBConnection(path)
    DBConnection(path)  # opening it again is harmless

    raw = sqlite3.connect(path)
    assert raw.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (0,)
    assert raw.execute("SELECT COUNT(*) FROM scenes").fetchone() == (2,)
    assert raw.execute("SELECT COUNT(*) FROM corrected").fetchone() == (1,)
    raw.close()
    assert migrated.scenes_without_metadata() == [(date(2018, 1, 2), "T29SPA", "S2/A")]


# --- spacecraft is a pattern, not a closed list ------------------------------------------

OLD_SPACECRAFT_CHECK = "CHECK (spacecraft IN ('S2A', 'S2B', 'S2C'))"
NEW_SPACECRAFT_CHECK = "CHECK (spacecraft GLOB 'S2[A-Z]')"


def insert_metadata(connection, scene_id, spacecraft):
    connection.execute(
        "INSERT INTO scene_metadata (scene_id, product_id, spacecraft, acquired_at, properties) "
        "VALUES (?, 'p', ?, '2026-09-21T10:50:31.024Z', '{\"a\": 1}')",
        (scene_id, spacecraft),
    )


def test_a_new_satellite_letter_is_accepted(raw_db, db):
    db.register_scene(DAY, TILE, GEE_ID)

    insert_metadata(raw_db, 1, "S2D")


def test_a_value_that_is_not_a_sentinel_2_unit_is_rejected(raw_db, db):
    db.register_scene(DAY, TILE, GEE_ID)

    with pytest.raises(sqlite3.IntegrityError):
        insert_metadata(raw_db, 1, "LS8")


def test_a_database_with_the_closed_spacecraft_list_is_rebuilt_keeping_its_rows(tmp_path):
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    assert NEW_SPACECRAFT_CHECK in schema
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(schema.replace(NEW_SPACECRAFT_CHECK, OLD_SPACECRAFT_CHECK))
    old.executescript(
        """
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (1, '2018-01-02', 'T29SPA', 'a');
        INSERT INTO scenes (id, date, tile, gee_id) VALUES (2, '2018-01-03', 'T29SPA', 'b');
        """
    )
    insert_metadata(old, 1, "S2A")
    insert_metadata(old, 2, "S2B")
    old.commit()
    with pytest.raises(sqlite3.IntegrityError):  # the old check really is the closed list
        insert_metadata(old, 3, "S2D")
    old.close()

    DBConnection(path)
    DBConnection(path)  # opening it again is harmless

    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = ON")
    sql = raw.execute("SELECT sql FROM sqlite_master WHERE name = 'scene_metadata'").fetchone()[0]
    rows = raw.execute("SELECT scene_id, spacecraft, json_valid(properties) FROM scene_metadata ORDER BY 1").fetchall()
    raw.execute("INSERT INTO scenes (id, date, tile, gee_id) VALUES (3, '2018-01-04', 'T29SPA', 'c')")
    insert_metadata(raw, 3, "S2D")
    with pytest.raises(sqlite3.IntegrityError):  # the other constraints survived the rebuild
        raw.execute(
            "INSERT INTO scene_metadata (scene_id, product_id, spacecraft, acquired_at, properties) "
            "VALUES (99, 'p', 'S2A', 't', '{}')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO scene_metadata (scene_id, product_id, spacecraft, acquired_at, properties) "
            "VALUES (2, 'p', 'S2A', 't', 'not json')"
        )
    raw.close()

    assert NEW_SPACECRAFT_CHECK in sql and OLD_SPACECRAFT_CHECK not in sql
    assert rows == [(1, "S2A", 1), (2, "S2B", 1)]
