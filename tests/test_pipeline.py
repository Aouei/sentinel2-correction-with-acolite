import time

import pytest

from src.pipeline import AcoliteConfig, Pipeline, RunStatus
from src.roi import hash_roi

from conftest import DAY, GEE_ID, OTHER_ROI, ROI, TILE


# --- fakes ----------------------------------------------------------------


PROPERTIES = {"PRODUCT_ID": "S2B_MSIL1C_x", "SPACECRAFT_NAME": "Sentinel-2B", "system:time_start": 1}


class FakeImage:
    """Stands in for ee.Image. The pipeline never calls it: it only hands it to `describe`."""

    def __init__(self, name="image", gee_id=GEE_ID):
        self.name = name
        self.gee_id = gee_id


class FakeTask:
    """Stands in for ee.batch.Task.

    `.status()["state"]` walks through `states`; an exception instance in `states` is raised
    instead. The last item repeats forever.
    """

    def __init__(self, states, events):
        self.states = list(states)
        self.events = events
        self.cancelled = False

    def status(self):
        item = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(item, BaseException):
            self.events.append("status:error")
            raise item
        self.events.append(f"status:{item}")
        return {"state": item, "error_message": "export exploded"}

    def cancel(self):
        self.cancelled = True
        self.events.append("cancel")


class FakeDB:
    def __init__(self, events, corrected=False):
        self.events = events
        self.corrected = corrected
        self.is_corrected_calls = []
        self.registered = []
        self.saved_metadata = []
        self.saved_metadata_gee_ids = []
        self.save_metadata_error = None
        self.marked_corrected = []
        self.marked_error = []
        self.logged_errors = []
        self.logged_tracebacks = []

    def is_corrected(self, date, tile, config, roi):
        self.is_corrected_calls.append((date, tile, config, roi))
        return self.corrected

    def register_scene(self, date, tile, gee_id):
        self.events.append("register_scene")
        self.registered.append((date, tile, gee_id))

    def save_scene_metadata(self, date, tile, properties, *, gee_id):
        self.events.append("save_scene_metadata")
        if self.save_metadata_error:
            raise self.save_metadata_error
        self.saved_metadata.append((date, tile, properties))
        self.saved_metadata_gee_ids.append(gee_id)

    def mark_corrected(self, date, tile, config, roi, asset_id):
        self.events.append("mark_corrected")
        self.marked_corrected.append((date, tile, config, roi, asset_id))

    def mark_error(self, date, tile, config, roi):
        self.events.append("mark_error")
        self.marked_error.append((date, tile, config, roi))

    def log_error(self, step, message, date, tile, config=None, traceback=None):
        self.events.append(f"log_error:{step}")
        self.logged_errors.append((step, message, date, tile, config))
        self.logged_tracebacks.append(traceback)


class Steps:
    """Spy steps. Every call is appended to `events` so ordering can be asserted."""

    def __init__(self, events, image=None, task_states=("COMPLETED",)):
        self.events = events
        self.image = FakeImage() if image is None else image
        self.task_states = task_states
        self.extract_calls = []
        self.describe_calls = []
        self.describe_error = None
        self.properties = PROPERTIES
        self.transform_calls = []
        self.load_calls = []
        self.transformed = FakeImage("transformed")
        self.extract_error = None
        self.transform_error = None
        self.load_error = None
        self.no_image = False
        self.tasks = []
        self.existing_assets = set()
        self.asset_exists_calls = []
        self.asset_exists_error = None

    def extract(self, date, tile, roi):
        self.events.append("extract")
        self.extract_calls.append((date, tile, roi))
        if self.extract_error:
            raise self.extract_error
        return None if self.no_image else self.image

    def describe(self, image):
        self.events.append("describe")
        self.describe_calls.append(image)
        if self.describe_error:
            raise self.describe_error
        return image.gee_id, self.properties

    def transform(self, image, config):
        self.events.append("transform")
        self.transform_calls.append((image, config))
        if self.transform_error:
            raise self.transform_error
        return self.transformed

    def load(self, image, asset_id, roi):
        self.events.append("load")
        self.load_calls.append((image, asset_id, roi))
        if self.load_error:
            raise self.load_error
        task = FakeTask(self.task_states, self.events)
        self.tasks.append(task)
        return task

    def asset_exists(self, asset_id):
        self.events.append("asset_exists")
        self.asset_exists_calls.append(asset_id)
        if self.asset_exists_error:
            raise self.asset_exists_error
        return asset_id in self.existing_assets


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The pipeline polls the export task; never really wait in tests."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


@pytest.fixture
def events():
    return []


@pytest.fixture
def steps(events):
    return Steps(events)


@pytest.fixture
def fake_db(events):
    return FakeDB(events)


def build(steps, db):
    return Pipeline(
        extract=steps.extract,
        describe=steps.describe,
        transform=steps.transform,
        load=steps.load,
        asset_exists=steps.asset_exists,
        db=db,
    )


def expected_asset_id(config, roi=ROI):
    config_hash, roi_hash = config.hash()[:12], hash_roi(roi)[:12]
    return f"projects/ee-sergiohercar1/assets/ETL/{DAY.isoformat()}_{TILE}_{config_hash}_{roi_hash}_L2W"


# --- already corrected ----------------------------------------------------


def test_already_corrected_returns_success_without_calling_any_step(steps, fake_db, config):
    fake_db.corrected = True

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert steps.extract_calls == []
    assert steps.transform_calls == []
    assert steps.load_calls == []


def test_is_corrected_is_asked_with_the_domain_keys(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.is_corrected_calls == [(DAY, TILE, config, ROI)]


def test_already_corrected_does_not_touch_the_database_again(steps, fake_db, config):
    fake_db.corrected = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.registered == []
    assert fake_db.marked_corrected == []
    assert fake_db.logged_errors == []


# --- no image -------------------------------------------------------------


def test_no_image_for_the_date_ends_successfully(steps, fake_db, config):
    steps.no_image = True

    assert build(steps, fake_db).run(DAY, TILE, config, ROI) is RunStatus.SUCCESS


def test_no_image_runs_no_further_step_and_only_records_the_attempt(steps, fake_db, config):
    steps.no_image = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.transform_calls == []
    assert steps.load_calls == []
    assert fake_db.registered == [(DAY, TILE, None)]  # searched: Earth Engine has no image
    assert fake_db.marked_corrected == []
    assert fake_db.marked_error == []
    assert fake_db.logged_errors == []


def test_no_image_is_never_described_and_has_no_metadata(steps, fake_db, config):
    steps.no_image = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.describe_calls == []
    assert fake_db.saved_metadata == []


def test_already_corrected_never_describes_an_image(steps, fake_db, config):
    fake_db.corrected = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.describe_calls == []
    assert fake_db.saved_metadata == []


# --- scene metadata -------------------------------------------------------


def test_the_extracted_image_is_described_exactly_once(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.describe_calls == [steps.image]


def test_the_source_image_properties_are_saved_for_the_scene(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.saved_metadata == [(DAY, TILE, PROPERTIES)]


def test_the_metadata_is_saved_against_the_gee_id_of_the_described_image(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.saved_metadata_gee_ids == [GEE_ID]


def test_an_image_other_than_the_registered_one_is_an_error_and_keeps_the_stored_metadata(
    events, db, raw_db, config
):
    steps = Steps(events)
    pipeline = build(steps, db)
    pipeline.run(DAY, TILE, config, ROI)
    steps.image = FakeImage(gee_id=GEE_ID + "_replacement")
    steps.properties = {**PROPERTIES, "PRODUCT_ID": "S2A_MSIL1C_replacement"}
    steps.load_calls.clear()
    raw_db.execute("DELETE FROM corrected")  # force a new attempt
    raw_db.commit()

    status = pipeline.run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert steps.load_calls == []
    assert raw_db.execute("SELECT product_id FROM scene_metadata").fetchall() == [("S2B_MSIL1C_x",)]
    assert "_replacement" in raw_db.execute("SELECT error_msg FROM errors").fetchone()[0]


def test_metadata_is_saved_right_after_registering_the_scene(steps, fake_db, config, events):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert events[:4] == ["extract", "describe", "register_scene", "save_scene_metadata"]


def test_metadata_is_kept_even_when_the_export_fails(steps, fake_db, config):
    steps.load_error = RuntimeError("export exploded")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.saved_metadata == [(DAY, TILE, PROPERTIES)]


def test_metadata_is_kept_when_the_asset_already_exists(steps, fake_db, config):
    steps.existing_assets.add(expected_asset_id(config))

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.saved_metadata == [(DAY, TILE, PROPERTIES)]


def test_a_failing_describe_is_an_extract_error_and_registers_nothing(steps, fake_db, config):
    steps.describe_error = RuntimeError("getInfo timed out")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert [step for step, *_rest in fake_db.logged_errors] == ["extract"]
    assert fake_db.registered == []
    assert fake_db.saved_metadata == []
    assert steps.transform_calls == []


def test_a_failing_metadata_save_is_an_extract_error_and_stops_the_run(steps, fake_db, config):
    fake_db.save_metadata_error = ValueError("the image has no PRODUCT_ID property")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert [step for step, *_rest in fake_db.logged_errors] == ["extract"]
    assert steps.transform_calls == []


def test_a_failing_metadata_save_marks_the_scene_as_error(steps, fake_db, config):
    fake_db.save_metadata_error = ValueError("the image has no PRODUCT_ID property")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.marked_error == [(DAY, TILE, config, ROI)]
    assert steps.load_calls == []


def test_a_failing_metadata_save_message_names_the_offending_property(steps, fake_db, config):
    fake_db.save_metadata_error = ValueError("the image has no PRODUCT_ID property")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    ((step, message, _date, _tile, logged_config),) = fake_db.logged_errors
    assert step == "extract"
    assert "scene metadata" in message and "PRODUCT_ID" in message
    assert logged_config is config


def test_a_metadata_failure_leaves_a_visible_error_state(events, db, raw_db, config):
    steps = Steps(events)
    steps.properties = {"SPACECRAFT_NAME": "Sentinel-2B", "system:time_start": 1}  # no PRODUCT_ID

    status = build(steps, db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert raw_db.execute("SELECT status FROM corrected").fetchall() == [("error",)]
    assert "PRODUCT_ID" in raw_db.execute("SELECT error_msg FROM errors").fetchone()[0]
    assert steps.load_calls == []


# --- happy path -----------------------------------------------------------


def test_happy_path_returns_success(steps, fake_db, config):
    assert build(steps, fake_db).run(DAY, TILE, config, ROI) is RunStatus.SUCCESS


def test_extract_receives_date_tile_and_roi(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.extract_calls == [(DAY, TILE, ROI)]


def test_scene_is_registered_with_the_gee_id_of_the_image(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.registered == [(DAY, TILE, GEE_ID)]


def test_transform_receives_the_extracted_image_and_the_config(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.transform_calls == [(steps.image, config)]


def test_load_receives_the_transformed_image_and_a_deterministic_asset_id(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.load_calls == [(steps.transformed, expected_asset_id(config), ROI)]


def test_the_asset_id_changes_with_the_config(steps, fake_db, config):
    other = AcoliteConfig(version="20240101.0", path=config.path, params=config.params)

    build(steps, fake_db).run(DAY, TILE, config, ROI)
    build(steps, fake_db).run(DAY, TILE, other, ROI)

    first, second = (asset_id for _, asset_id, _roi in steps.load_calls)
    assert first != second


def test_corrected_is_marked_with_the_asset_id(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.marked_corrected == [(DAY, TILE, config, ROI, expected_asset_id(config))]


def test_steps_run_in_order_and_corrected_is_marked_last(steps, fake_db, events, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert events == [
        "extract",
        "describe",
        "register_scene",
        "save_scene_metadata",
        "asset_exists",
        "transform",
        "load",
        "status:COMPLETED",
        "mark_corrected",
    ]


# --- waiting for the export task -------------------------------------------


def test_waits_until_the_task_is_completed_before_marking_corrected(events, fake_db, config):
    steps = Steps(events, task_states=("READY", "RUNNING", "COMPLETED"))

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert events[-4:] == ["status:READY", "status:RUNNING", "status:COMPLETED", "mark_corrected"]


@pytest.mark.parametrize("final_state", ["FAILED", "CANCELLED"])
def test_a_task_that_does_not_complete_is_a_failure(events, fake_db, config, final_state):
    steps = Steps(events, task_states=("RUNNING", final_state))

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert fake_db.marked_corrected == []
    assert fake_db.marked_error == [(DAY, TILE, config, ROI)]
    assert [step for step, *_ in fake_db.logged_errors] == ["load"]


# --- errors ---------------------------------------------------------------


def test_extract_error_returns_fail_and_is_logged(steps, fake_db, config):
    steps.extract_error = RuntimeError("gee down")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    [(step, message, date, tile, _config)] = fake_db.logged_errors
    assert (step, date, tile) == ("extract", DAY, TILE)
    assert "gee down" in message


def test_extract_error_does_not_mark_anything_because_the_scene_is_not_registered(steps, fake_db, config):
    steps.extract_error = RuntimeError("gee down")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert fake_db.registered == []
    assert fake_db.marked_error == []
    assert steps.transform_calls == []
    assert steps.load_calls == []


def test_transform_error_returns_fail_marks_error_and_skips_load(steps, fake_db, config):
    steps.transform_error = ValueError("acolite crashed")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert fake_db.marked_error == [(DAY, TILE, config, ROI)]
    assert steps.load_calls == []
    [(step, message, date, tile, logged_config)] = fake_db.logged_errors
    assert (step, date, tile, logged_config) == ("transform", DAY, TILE, config)
    assert "acolite crashed" in message


def test_transform_error_happens_after_the_scene_was_registered(steps, fake_db, events, config):
    steps.transform_error = ValueError("acolite crashed")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert events.index("register_scene") < events.index("transform")
    assert fake_db.registered == [(DAY, TILE, GEE_ID)]


def test_load_error_returns_fail_and_marks_error(steps, fake_db, config):
    steps.load_error = OSError("cannot start export")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert fake_db.marked_corrected == []
    assert fake_db.marked_error == [(DAY, TILE, config, ROI)]
    [(step, message, *_rest)] = fake_db.logged_errors
    assert step == "load"
    assert "cannot start export" in message


def test_run_never_raises_when_a_step_fails(steps, fake_db, config):
    steps.extract_error = Exception("anything")

    build(steps, fake_db).run(DAY, TILE, config, ROI)


# --- idempotency end to end (real DBConnection) -----------------------------


def test_a_second_run_does_not_reprocess(events, db, config):
    steps = Steps(events)
    pipeline = build(steps, db)

    first = pipeline.run(DAY, TILE, config, ROI)
    second = pipeline.run(DAY, TILE, config, ROI)

    assert (first, second) == (RunStatus.SUCCESS, RunStatus.SUCCESS)
    assert len(steps.extract_calls) == 1
    assert len(steps.load_calls) == 1


def test_a_failed_run_is_retried_on_the_next_run(events, db, config):
    steps = Steps(events, task_states=("FAILED",))
    pipeline = build(steps, db)
    assert pipeline.run(DAY, TILE, config, ROI) is RunStatus.FAIL

    steps.task_states = ("COMPLETED",)

    assert pipeline.run(DAY, TILE, config, ROI) is RunStatus.SUCCESS
    assert len(steps.extract_calls) == 2
    assert db.is_corrected(DAY, TILE, config, ROI) is True


def test_a_different_config_is_processed_again(events, db, config):
    steps = Steps(events)
    pipeline = build(steps, db)
    other = AcoliteConfig(version="20240101.0", path=config.path, params=config.params)

    pipeline.run(DAY, TILE, config, ROI)
    pipeline.run(DAY, TILE, other, ROI)

    assert len(steps.load_calls) == 2


def test_a_different_tile_on_the_same_date_is_processed_again(events, db, config):
    steps = Steps(events)
    pipeline = build(steps, db)

    pipeline.run(DAY, "30SXG", config, ROI)
    steps.image = FakeImage(gee_id=GEE_ID + "_other_tile")
    pipeline.run(DAY, "30SYG", config, ROI)

    assert len(steps.load_calls) == 2


def test_errors_are_persisted_with_date_and_tile(events, db, raw_db, config):
    steps = Steps(events)
    steps.extract_error = RuntimeError("gee down")

    build(steps, db).run(DAY, TILE, config, ROI)

    assert raw_db.execute("SELECT step, date, tile FROM errors").fetchall() == [
        ("extract", DAY.isoformat(), TILE)
    ]


# --- the ROI is part of the identity of a correction -------------------------


def test_load_receives_the_roi(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, OTHER_ROI)

    assert steps.load_calls[0][2] == OTHER_ROI


def test_the_asset_id_changes_with_the_roi(steps, fake_db, config):
    pipeline = build(steps, fake_db)

    pipeline.run(DAY, TILE, config, ROI)
    pipeline.run(DAY, TILE, config, OTHER_ROI)

    first, second = (asset_id for _, asset_id, _roi in steps.load_calls)
    assert first == expected_asset_id(config, ROI)
    assert second == expected_asset_id(config, OTHER_ROI)
    assert first != second


def test_a_different_roi_is_processed_again(events, db, config):
    steps = Steps(events)
    pipeline = build(steps, db)

    first = pipeline.run(DAY, TILE, config, ROI)
    second = pipeline.run(DAY, TILE, config, OTHER_ROI)

    assert (first, second) == (RunStatus.SUCCESS, RunStatus.SUCCESS)
    assert len(steps.extract_calls) == 2
    assert len(steps.load_calls) == 2


def test_the_same_roi_with_another_key_order_does_not_trigger_reprocessing(events, db, config):
    steps = Steps(events)
    pipeline = build(steps, db)
    reordered = {"coordinates": ROI["coordinates"], "type": ROI["type"]}

    pipeline.run(DAY, TILE, config, ROI)
    pipeline.run(DAY, TILE, config, reordered)

    assert len(steps.extract_calls) == 1
    assert len(steps.load_calls) == 1


# --- a lost export must not block the next run --------------------------------


def test_the_asset_check_uses_the_deterministic_asset_id(steps, fake_db, config):
    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.asset_exists_calls == [expected_asset_id(config)]


def test_an_existing_asset_is_marked_corrected_without_exporting_again(steps, fake_db, config):
    steps.existing_assets = {expected_asset_id(config)}

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert steps.transform_calls == []
    assert steps.load_calls == []
    assert fake_db.marked_corrected == [(DAY, TILE, config, ROI, expected_asset_id(config))]


def test_the_asset_is_not_checked_when_there_is_no_image(steps, fake_db, config):
    steps.no_image = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.asset_exists_calls == []


def test_the_asset_is_not_checked_when_it_is_already_corrected(steps, fake_db, config):
    fake_db.corrected = True

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.asset_exists_calls == []


def test_an_error_checking_the_asset_is_a_load_failure(steps, fake_db, config):
    steps.asset_exists_error = RuntimeError("gee down")

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert steps.transform_calls == []
    assert fake_db.marked_error == [(DAY, TILE, config, ROI)]
    [(step, message, *_rest)] = fake_db.logged_errors
    assert step == "load"
    assert "gee down" in message


def test_a_lost_export_is_adopted_on_the_next_run(events, db, config):
    steps = Steps(events, task_states=("FAILED",))
    pipeline = build(steps, db)
    assert pipeline.run(DAY, TILE, config, ROI) is RunStatus.FAIL

    steps.existing_assets = {expected_asset_id(config)}  # the export finished anyway

    assert pipeline.run(DAY, TILE, config, ROI) is RunStatus.SUCCESS
    assert len(steps.transform_calls) == 1  # it was not corrected a second time
    assert db.is_corrected(DAY, TILE, config, ROI) is True


# --- waiting for the export task is resilient --------------------------------


def test_transient_errors_reading_the_task_are_retried(events, fake_db, config):
    flaky = ConnectionError("503")
    steps = Steps(events, task_states=(flaky, flaky, "RUNNING", "COMPLETED"))

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert fake_db.marked_corrected == [(DAY, TILE, config, ROI, expected_asset_id(config))]


def test_the_error_counter_resets_after_a_good_read(events, fake_db, config):
    flaky = ConnectionError("503")
    states = (flaky,) * 4 + ("RUNNING",) + (flaky,) * 4 + ("COMPLETED",)
    steps = Steps(events, task_states=states)

    assert build(steps, fake_db).run(DAY, TILE, config, ROI) is RunStatus.SUCCESS


def test_too_many_consecutive_errors_reading_the_task_are_a_load_failure(events, fake_db, config):
    steps = Steps(events, task_states=(ConnectionError("503 unavailable"),))

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert fake_db.marked_corrected == []
    [(step, message, *_rest)] = fake_db.logged_errors
    assert step == "load"
    assert "503 unavailable" in message


def test_a_task_that_never_finishes_is_cancelled_after_the_timeout(events, fake_db, config, monkeypatch):
    monkeypatch.setattr("src.pipeline.TASK_TIMEOUT_SECONDS", 30)
    steps = Steps(events, task_states=("READY",))

    status = build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.FAIL
    assert steps.tasks[0].cancelled is True
    assert fake_db.marked_corrected == []
    [(step, message, *_rest)] = fake_db.logged_errors
    assert step == "load"
    assert "timed out" in message


def test_interrupting_the_wait_cancels_the_task_and_propagates(events, fake_db, config):
    steps = Steps(events, task_states=(KeyboardInterrupt(),))

    with pytest.raises(KeyboardInterrupt):
        build(steps, fake_db).run(DAY, TILE, config, ROI)

    assert steps.tasks[0].cancelled is True
    assert fake_db.marked_corrected == []


# --- the traceback of every failure is recorded -------------------------------


def assert_traceback(text, error_type, message):
    assert text is not None
    assert "Traceback (most recent call last)" in text
    assert error_type in text
    assert message in text


def test_the_traceback_of_an_extract_error_is_logged(steps, fake_db, config):
    steps.extract_error = RuntimeError("gee down")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    [text] = fake_db.logged_tracebacks
    assert_traceback(text, "RuntimeError", "gee down")


def test_the_traceback_of_a_transform_error_is_logged(steps, fake_db, config):
    steps.transform_error = ValueError("acolite crashed")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    [text] = fake_db.logged_tracebacks
    assert_traceback(text, "ValueError", "acolite crashed")


def test_the_traceback_of_a_load_error_is_logged(steps, fake_db, config):
    steps.load_error = OSError("cannot start export")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    [text] = fake_db.logged_tracebacks
    assert_traceback(text, "OSError", "cannot start export")


def test_the_traceback_of_an_asset_check_error_is_logged(steps, fake_db, config):
    steps.asset_exists_error = RuntimeError("gee down")

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    [text] = fake_db.logged_tracebacks
    assert_traceback(text, "RuntimeError", "gee down")


def test_the_traceback_of_a_failed_export_task_is_logged(events, fake_db, config):
    steps = Steps(events, task_states=("FAILED",))

    build(steps, fake_db).run(DAY, TILE, config, ROI)

    [text] = fake_db.logged_tracebacks
    assert_traceback(text, "ExportError", "export exploded")


def test_the_traceback_is_persisted_with_the_error(events, db, raw_db, config):
    steps = Steps(events)
    steps.transform_error = ValueError("acolite crashed")

    build(steps, db).run(DAY, TILE, config, ROI)

    [(text,)] = raw_db.execute("SELECT traceback FROM errors").fetchall()
    assert_traceback(text, "ValueError", "acolite crashed")


# --- a date without image is a recorded attempt --------------------------------


def scene_rows(raw_db):
    return raw_db.execute("SELECT date, tile, gee_id FROM scenes").fetchall()


def test_a_date_without_image_is_recorded_with_a_null_gee_id(events, db, raw_db, config):
    steps = Steps(events)
    steps.no_image = True

    status = build(steps, db).run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert scene_rows(raw_db) == [(DAY.isoformat(), TILE, None)]


def test_searching_a_date_without_image_again_keeps_a_single_row(events, db, raw_db, config):
    steps = Steps(events)
    steps.no_image = True
    pipeline = build(steps, db)

    pipeline.run(DAY, TILE, config, ROI)
    pipeline.run(DAY, TILE, config, ROI)

    assert scene_rows(raw_db) == [(DAY.isoformat(), TILE, None)]


def test_an_image_that_shows_up_later_fills_the_null_gee_id(events, db, raw_db, config):
    steps = Steps(events)
    steps.no_image = True
    pipeline = build(steps, db)
    pipeline.run(DAY, TILE, config, ROI)

    steps.no_image = False
    status = pipeline.run(DAY, TILE, config, ROI)

    assert status is RunStatus.SUCCESS
    assert scene_rows(raw_db) == [(DAY.isoformat(), TILE, GEE_ID)]
    assert db.is_corrected(DAY, TILE, config, ROI) is True


def test_a_failed_search_leaves_no_row_because_the_date_was_not_really_tried(events, db, raw_db, config):
    steps = Steps(events)
    steps.extract_error = RuntimeError("gee down")

    build(steps, db).run(DAY, TILE, config, ROI)

    assert scene_rows(raw_db) == []
