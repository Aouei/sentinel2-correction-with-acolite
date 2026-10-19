import argparse
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as cli
from src.pipeline import AcoliteConfig, Pipeline, RunStatus

from conftest import DAY, ROI, TILE

SETTINGS = {"l2w_parameters": "Rrs_*", "s2_target_res": 10, "EARTHDATA_u": "user"}
REQUIRED = ["date", "tile", "roi", "settings", "acolite-version", "acolite-path"]


@pytest.fixture
def files(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(SETTINGS), encoding="utf-8")
    roi = tmp_path / "roi.json"
    feature = {"type": "Feature", "properties": {}, "geometry": ROI}
    roi.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8")
    return SimpleNamespace(
        settings=settings, roi=roi, acolite=tmp_path / "acolite", db=tmp_path / "state.db"
    )


def make_argv(files, replace=None, without=()):
    values = {
        "date": "2026-09-21",
        "tile": TILE,
        "roi": str(files.roi),
        "settings": str(files.settings),
        "acolite-version": "20231023.0",
        "acolite-path": str(files.acolite),
        "db": str(files.db),
    }
    values.update(replace or {})
    argv = []
    for name, value in values.items():
        if name not in without:
            argv += [f"--{name}", value]
    return argv


class FakePipeline:
    def __init__(self, status):
        self.status = status
        self.calls = []

    def run(self, date, tile, config, roi):
        self.calls.append((date, tile, config, roi))
        return self.status


@pytest.fixture
def pipeline(monkeypatch):
    fake = FakePipeline(RunStatus.SUCCESS)
    fake.built = []

    def fake_build(db_path):
        fake.built.append(db_path)
        return fake

    monkeypatch.setattr(cli, "build_pipeline", fake_build)
    return fake


# --- arguments ---------------------------------------------------------------


def test_parses_every_argument(files):
    args = cli.build_parser().parse_args(make_argv(files))

    assert args.date == DAY
    assert args.tile == TILE
    assert args.roi == files.roi
    assert args.settings == files.settings
    assert args.acolite_version == "20231023.0"
    assert args.acolite_path == files.acolite
    assert args.db == files.db


def test_paths_are_parsed_as_paths(files):
    args = cli.build_parser().parse_args(make_argv(files))

    assert all(isinstance(value, Path) for value in (args.roi, args.settings, args.acolite_path, args.db))


def test_db_defaults_to_state_db(files):
    args = cli.build_parser().parse_args(make_argv(files, without=["db"]))

    assert args.db == Path("state.db")


@pytest.mark.parametrize("name", REQUIRED)
def test_every_required_argument_is_mandatory(files, name):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(make_argv(files, without=[name]))

    assert error.value.code == 2


@pytest.mark.parametrize(
    "value",
    ["2026-09-21T10:00:00", "2026-09-21 10:00:00", "21/09/2026", "2026-9-1", "20260921", "2026-02-30", ""],
)
def test_date_must_be_a_plain_yyyy_mm_dd_date(files, value):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(make_argv(files, replace={"date": value}))

    assert error.value.code == 2


def test_iso_date_returns_a_date():
    assert cli.iso_date("2026-09-21") == date(2026, 9, 21)


def test_iso_date_raises_an_argparse_error_for_a_datetime():
    with pytest.raises(argparse.ArgumentTypeError):
        cli.iso_date("2026-09-21T10:00:00")


# --- input files ---------------------------------------------------------------


def test_load_settings_returns_the_json_object(files):
    assert cli.load_settings(files.settings) == SETTINGS


def test_load_settings_rejects_something_that_is_not_an_object(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(ValueError):
        cli.load_settings(path)


def test_load_roi_returns_the_geometry_of_the_single_feature(files):
    assert cli.load_roi(files.roi) == ROI


def test_load_roi_rejects_a_collection_without_features(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        cli.load_roi(path)


def test_load_roi_rejects_something_that_is_not_a_feature_collection(tmp_path):
    path = tmp_path / "geometry.json"
    path.write_text(json.dumps(ROI), encoding="utf-8")

    with pytest.raises(ValueError):
        cli.load_roi(path)


# --- main ------------------------------------------------------------------------


def test_main_runs_the_pipeline_with_the_parsed_arguments(files, pipeline):
    cli.main(make_argv(files))

    expected = AcoliteConfig(version="20231023.0", path=files.acolite, params=SETTINGS)
    assert pipeline.calls == [(DAY, TILE, expected, ROI)]


def test_main_builds_the_pipeline_with_the_database_path(files, pipeline):
    cli.main(make_argv(files))

    assert pipeline.built == [files.db]


def test_main_returns_0_when_the_run_succeeds(files, pipeline):
    pipeline.status = RunStatus.SUCCESS

    assert cli.main(make_argv(files)) == 0


def test_main_returns_1_when_the_run_fails(files, pipeline):
    pipeline.status = RunStatus.FAIL

    assert cli.main(make_argv(files)) == 1


def test_main_prints_the_run_status(files, pipeline, capsys):
    pipeline.status = RunStatus.FAIL

    cli.main(make_argv(files))

    assert "FAIL" in capsys.readouterr().out


def test_main_returns_2_when_the_settings_file_does_not_exist(files, pipeline, capsys):
    argv = make_argv(files, replace={"settings": str(files.settings.with_name("missing.json"))})

    assert cli.main(argv) == 2
    assert "missing.json" in capsys.readouterr().err


def test_main_returns_2_when_the_settings_file_is_not_valid_json(files, pipeline):
    files.settings.write_text("{not json", encoding="utf-8")

    assert cli.main(make_argv(files)) == 2


def test_main_returns_2_when_the_roi_file_does_not_exist(files, pipeline, capsys):
    argv = make_argv(files, replace={"roi": str(files.roi.with_name("missing_roi.json"))})

    assert cli.main(argv) == 2
    assert "missing_roi.json" in capsys.readouterr().err


def test_main_returns_2_when_the_roi_has_no_features(files, pipeline):
    files.roi.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")

    assert cli.main(make_argv(files)) == 2


def test_invalid_inputs_never_build_nor_run_the_pipeline(files, pipeline):
    files.settings.write_text("{not json", encoding="utf-8")

    cli.main(make_argv(files))

    assert pipeline.built == []
    assert pipeline.calls == []


# --- build_pipeline ----------------------------------------------------------------


def test_build_pipeline_initializes_earth_engine_for_the_project(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "ee", SimpleNamespace(Initialize=lambda **kwargs: calls.append(kwargs)))

    cli.build_pipeline(tmp_path / "state.db")

    assert calls == [{"project": "ee-sergiohercar1"}]


def test_build_pipeline_raises_a_clear_error_when_earth_engine_cannot_be_initialized(monkeypatch, tmp_path):
    attempts = []

    def initialize(**kwargs):
        raise RuntimeError("offline")

    fake_ee = SimpleNamespace(Initialize=initialize, Authenticate=lambda: attempts.append("authenticate"))
    monkeypatch.setattr(cli, "ee", fake_ee)

    with pytest.raises(cli.EarthEngineInitError, match="offline"):
        cli.build_pipeline(tmp_path / "state.db")

    assert attempts == []  # never opens an interactive login, so a batch cannot hang


def test_main_returns_3_when_earth_engine_is_not_available(files, monkeypatch, capsys):
    def unavailable(db_path):
        raise cli.EarthEngineInitError("offline")

    monkeypatch.setattr(cli, "build_pipeline", unavailable)

    assert cli.main(make_argv(files)) == 3
    assert "earthengine authenticate" in capsys.readouterr().err


def test_build_pipeline_returns_a_pipeline_backed_by_the_database(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ee", SimpleNamespace(Initialize=lambda **kwargs: None))
    db_path = tmp_path / "state.db"

    result = cli.build_pipeline(db_path)

    assert isinstance(result, Pipeline)
    assert db_path.exists()
