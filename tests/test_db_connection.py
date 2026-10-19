import sqlite3
from datetime import date

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
