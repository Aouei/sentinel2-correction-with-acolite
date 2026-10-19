from datetime import date

import pytest

from src.steps import extract as extract_module
from src.steps.extract import extract

from conftest import DAY, ROI, TILE
from fakes import FakeCollection, FakeImage


class FakeGeometry:
    def __init__(self, geojson):
        self.geojson = geojson


class FakeEE:
    Geometry = FakeGeometry


class SearchSpy:
    """Same signature as gee_acolite.utils.search; records how it was called."""

    def __init__(self, collection):
        self.collection = collection
        self.calls = []

    def __call__(self, roi, start, end, collection="S2_HARMONIZED", tile=None):
        self.calls.append({"roi": roi, "start": start, "end": end, "collection": collection, "tile": tile})
        return self.collection


@pytest.fixture
def images():
    return [FakeImage("first"), FakeImage("second")]


@pytest.fixture
def spy(monkeypatch, images):
    spy = SearchSpy(FakeCollection(images[:1]))
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", spy)
    return spy


def test_searches_the_whole_day_with_an_exclusive_end(spy):
    extract(date(2026, 9, 21), TILE, ROI)

    [call] = spy.calls
    assert (call["start"], call["end"]) == ("2026-09-21", "2026-09-22")


def test_the_end_of_the_window_rolls_over_the_month(spy):
    extract(date(2026, 9, 30), TILE, ROI)

    assert spy.calls[0]["end"] == "2026-10-01"


def test_searches_only_the_requested_tile(spy):
    extract(DAY, TILE, ROI)

    assert spy.calls[0]["tile"] == TILE


def test_converts_the_roi_with_ee_geometry(spy):
    extract(DAY, TILE, ROI)

    roi = spy.calls[0]["roi"]
    assert isinstance(roi, FakeGeometry)
    assert roi.geojson == ROI


def test_searches_the_default_l1c_collection(spy):
    extract(DAY, TILE, ROI)

    assert spy.calls[0]["collection"] == "S2_HARMONIZED"


def test_returns_the_first_image(spy, images):
    assert extract(DAY, TILE, ROI) is images[0]


def test_returns_the_most_recent_product_when_the_day_has_more_than_one(monkeypatch):
    older = FakeImage("older", {"GENERATION_TIME": 1523539296000})
    newer = FakeImage("newer", {"GENERATION_TIME": 1523540318000})
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", SearchSpy(FakeCollection([older, newer])))

    assert extract(DAY, TILE, ROI) is newer


def test_the_most_recent_product_wins_whatever_the_order_of_the_search(monkeypatch):
    older = FakeImage("older", {"GENERATION_TIME": 1523539296000})
    newer = FakeImage("newer", {"GENERATION_TIME": 1523540318000})
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", SearchSpy(FakeCollection([newer, older])))

    assert extract(DAY, TILE, ROI) is newer


def test_sorts_by_generation_time_descending(monkeypatch):
    collection = FakeCollection([FakeImage("a"), FakeImage("b")])
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", SearchSpy(collection))

    extract(DAY, TILE, ROI)

    assert collection.sort_calls == [("GENERATION_TIME", False)]


def test_does_not_sort_when_there_is_no_image(monkeypatch):
    collection = FakeCollection([])
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", SearchSpy(collection))

    assert extract(DAY, TILE, ROI) is None
    assert collection.sort_calls == []


def test_returns_none_when_there_is_no_image(monkeypatch):
    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", SearchSpy(FakeCollection([])))

    assert extract(DAY, TILE, ROI) is None


def test_search_errors_propagate(monkeypatch):
    def broken_search(*args, **kwargs):
        raise RuntimeError("gee down")

    monkeypatch.setattr(extract_module, "ee", FakeEE)
    monkeypatch.setattr(extract_module, "search", broken_search)

    with pytest.raises(RuntimeError, match="gee down"):
        extract(DAY, TILE, ROI)
