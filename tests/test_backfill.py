import sqlite3
from datetime import date

import pytest

from scripts import backfill_scene_metadata as backfill_script
from scripts.backfill_scene_metadata import backfill

from conftest import DAY, GEE_ID, TILE
from test_db_connection import SOURCE_PROPERTIES

NEXT_DAY = date(2026, 9, 22)
NEXT_ID = GEE_ID + "_next"


class FakeFetcher:
    """Same signature as fetch_properties(gee_id); no network. `errors` maps a gee_id to its failure."""

    def __init__(self, errors=None, properties=None):
        self.calls = []
        self.errors = errors or {}
        self.properties = properties or SOURCE_PROPERTIES

    def __call__(self, gee_id):
        self.calls.append(gee_id)
        if gee_id in self.errors:
            raise self.errors[gee_id]
        return self.properties


@pytest.fixture
def scenes(db):
    db.register_scene(DAY, TILE, GEE_ID)
    db.register_scene(NEXT_DAY, TILE, NEXT_ID)
    db.register_scene(date(2026, 9, 23), TILE, None)  # searched, no image: never gets metadata


def metadata_count(raw_db):
    return raw_db.execute("SELECT COUNT(*) FROM scene_metadata").fetchone()[0]


# --- backfill ------------------------------------------------------------------


def test_fills_the_metadata_of_every_scene_with_image(db, raw_db, scenes):
    fetch = FakeFetcher()

    result = backfill(db, fetch)

    assert fetch.calls == [GEE_ID, NEXT_ID]
    assert metadata_count(raw_db) == 2
    assert result.saved == 2
    assert result.failed == []


def test_a_scene_without_image_gets_no_row(db, raw_db, scenes):
    backfill(db, FakeFetcher())

    assert raw_db.execute(
        "SELECT COUNT(*) FROM scene_metadata m JOIN scenes s ON s.id = m.scene_id WHERE s.gee_id IS NULL"
    ).fetchone() == (0,)


def test_running_it_again_fetches_and_inserts_nothing(db, raw_db, scenes):
    backfill(db, FakeFetcher())
    fetch = FakeFetcher()

    result = backfill(db, fetch)

    assert fetch.calls == []
    assert result.saved == 0
    assert metadata_count(raw_db) == 2


def test_scenes_that_already_have_metadata_are_not_fetched(db, scenes):
    db.save_scene_metadata(DAY, TILE, SOURCE_PROPERTIES)
    fetch = FakeFetcher()

    backfill(db, fetch)

    assert fetch.calls == [NEXT_ID]


def test_a_failing_scene_does_not_stop_the_others(db, raw_db, scenes):
    fetch = FakeFetcher(errors={GEE_ID: RuntimeError("Earth Engine: image not found")})

    result = backfill(db, fetch)

    assert fetch.calls == [GEE_ID, NEXT_ID]
    assert result.saved == 1
    assert [(day, tile, gee_id) for day, tile, gee_id, _error in result.failed] == [(DAY, TILE, GEE_ID)]
    assert "image not found" in result.failed[0][3]
    assert metadata_count(raw_db) == 1


def test_properties_that_cannot_be_stored_count_as_a_failure(db, raw_db, scenes):
    incomplete = {key: value for key, value in SOURCE_PROPERTIES.items() if key != "PRODUCT_ID"}

    result = backfill(db, FakeFetcher(properties=incomplete))

    assert result.saved == 0
    assert len(result.failed) == 2
    assert metadata_count(raw_db) == 0


def test_a_failed_scene_is_retried_by_the_next_run(db, raw_db, scenes):
    backfill(db, FakeFetcher(errors={GEE_ID: RuntimeError("boom")}))

    result = backfill(db, FakeFetcher())

    assert result.saved == 1
    assert metadata_count(raw_db) == 2


# --- main ----------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch):
    """Runs main() against a fake fetcher and a no-op Earth Engine initialization."""
    fetch = FakeFetcher()
    monkeypatch.setattr(backfill_script, "_initialize_earth_engine", lambda: None)
    monkeypatch.setattr(backfill_script, "fetch_properties", fetch)
    return fetch


def test_main_backfills_the_database_and_returns_0(db_path, scenes, cli, capsys):
    assert backfill_script.main(["--db", str(db_path)]) == 0

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT COUNT(*) FROM scene_metadata").fetchone() == (2,)
    connection.close()
    assert "2 saved" in capsys.readouterr().out


def test_main_returns_1_and_reports_the_scenes_that_failed(db_path, scenes, cli, capsys):
    cli.errors[GEE_ID] = RuntimeError("image not found")

    assert backfill_script.main(["--db", str(db_path)]) == 1

    captured = capsys.readouterr()
    assert "1 saved" in captured.out
    assert GEE_ID in captured.err
    assert "image not found" in captured.err


def test_main_returns_2_for_a_database_that_does_not_exist_and_does_not_create_it(tmp_path, cli, capsys):
    path = tmp_path / "missing.db"

    assert backfill_script.main(["--db", str(path)]) == 2

    assert not path.exists()
    assert "missing.db" in capsys.readouterr().err


def test_main_returns_3_when_earth_engine_is_not_available(db_path, scenes, monkeypatch, capsys):
    def unavailable():
        raise backfill_script.EarthEngineInitError("offline")

    monkeypatch.setattr(backfill_script, "_initialize_earth_engine", unavailable)

    assert backfill_script.main(["--db", str(db_path)]) == 3
    assert "earthengine authenticate" in capsys.readouterr().err


def test_main_does_not_start_earth_engine_when_there_is_nothing_to_backfill(db_path, db, cli, monkeypatch):
    started = []
    monkeypatch.setattr(backfill_script, "_initialize_earth_engine", lambda: started.append(True))

    assert backfill_script.main(["--db", str(db_path)]) == 0

    assert started == []
