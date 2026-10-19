from types import SimpleNamespace

import pytest

from src.steps import load as load_module
from src.steps.load import load

from conftest import ROI

ASSET_ID = "projects/ee-sergiohercar1/assets/ETL/2026-09-21_30SXG_" + "a" * 12 + "_" + "b" * 12 + "_L2W"

# Rrs_B1..Rrs_B7, TUR_B4 (665 nm), chl_oc3, pSDB green and pSDB red.
EXPORTED_BANDS = [
    "Rrs_B1",
    "Rrs_B2",
    "Rrs_B3",
    "Rrs_B4",
    "Rrs_B5",
    "Rrs_B6",
    "Rrs_B7",
    "TUR_Nechad2016_665",
    "chl_oc3",
    "pSDB_green",
    "pSDB_red",
]


class FakeTask:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


class FakeSelection:
    """The image that gets exported: the result of `.select(bands)`."""


class FakeGeometry:
    def __init__(self, geojson):
        self.geojson = geojson


class FakeImage:
    """Stands in for ee.Image: `.select(bands)` returns the image that gets exported."""

    def __init__(self):
        self.select_calls = []
        self.selection = FakeSelection()

    def select(self, bands):
        self.select_calls.append(list(bands))
        return self.selection


class ToAssetSpy:
    def __init__(self):
        self.calls = []
        self.task = FakeTask()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.task


@pytest.fixture
def to_asset(monkeypatch):
    spy = ToAssetSpy()
    fake_ee = SimpleNamespace(
        Geometry=FakeGeometry,
        batch=SimpleNamespace(Export=SimpleNamespace(image=SimpleNamespace(toAsset=spy))),
    )
    monkeypatch.setattr(load_module, "ee", fake_ee)
    return spy


def test_exports_to_the_given_asset_id(to_asset):
    load(FakeImage(), ASSET_ID, ROI)

    [call] = to_asset.calls
    assert call["assetId"] == ASSET_ID


def test_exports_only_the_requested_variables(to_asset):
    image = FakeImage()

    load(image, ASSET_ID, ROI)

    assert image.select_calls == [EXPORTED_BANDS]
    assert to_asset.calls[0]["image"] is image.selection


def test_exports_only_the_rrs_bands_from_b1_to_b7(to_asset):
    image = FakeImage()

    load(image, ASSET_ID, ROI)

    [selected] = image.select_calls
    rrs_bands = [band for band in selected if band.startswith("Rrs")]
    assert rrs_bands == [f"Rrs_B{number}" for number in range(1, 8)]


def test_does_not_export_the_other_bands(to_asset):
    image = FakeImage()

    load(image, ASSET_ID, ROI)

    [selected] = image.select_calls
    for unwanted in ("rhot", "rhos", "SPM", "chl_oc2", "glint"):
        assert not any(unwanted in band for band in selected)


def test_exports_in_epsg_4326(to_asset):
    load(FakeImage(), ASSET_ID, ROI)

    assert to_asset.calls[0]["crs"] == "EPSG:4326"


def test_exports_only_the_region_of_the_roi(to_asset):
    load(FakeImage(), ASSET_ID, ROI)

    region = to_asset.calls[0]["region"]
    assert isinstance(region, FakeGeometry)
    assert region.geojson == ROI


def test_exports_at_10_meters(to_asset):
    load(FakeImage(), ASSET_ID, ROI)

    assert to_asset.calls[0]["scale"] == 10


def test_the_task_description_is_the_asset_name(to_asset):
    load(FakeImage(), ASSET_ID, ROI)

    description = to_asset.calls[0]["description"]
    assert description == ASSET_ID.rsplit("/", 1)[-1]
    assert len(description) <= 100  # Earth Engine limit for task descriptions


def test_starts_the_task_and_returns_it(to_asset):
    task = load(FakeImage(), ASSET_ID, ROI)

    assert task is to_asset.task
    assert task.started is True


def test_does_not_start_anything_when_the_export_cannot_be_created(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("cannot create export")

    fake_ee = SimpleNamespace(
        Geometry=FakeGeometry,
        batch=SimpleNamespace(Export=SimpleNamespace(image=SimpleNamespace(toAsset=broken))),
    )
    monkeypatch.setattr(load_module, "ee", fake_ee)

    with pytest.raises(RuntimeError, match="cannot create export"):
        load(FakeImage(), ASSET_ID, ROI)
