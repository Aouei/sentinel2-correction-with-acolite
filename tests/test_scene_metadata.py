import json

import pytest

from src.scene_metadata import scene_metadata_columns

# system:time_start of 2018-01-02T11:14:39.024Z, as Earth Engine reports it (ms since the epoch)
TIME_START = 1514891679024


def properties(**overrides):
    values = {
        "PRODUCT_ID": "S2B_MSIL1C_20180102T111439_N0500_R137_T29SPA_20230906T202441",
        "SPACECRAFT_NAME": "Sentinel-2B",
        "PROCESSING_BASELINE": "05.00",
        "SENSING_ORBIT_NUMBER": 137,
        "system:time_start": TIME_START,
        "CLOUDY_PIXEL_PERCENTAGE": 12.5,
        "CLOUD_COVERAGE_ASSESSMENT": 12.25,
        "MEAN_SOLAR_ZENITH_ANGLE": 61.817131843863,
        "MEAN_SOLAR_AZIMUTH_ANGLE": 161.147975508761,
        "NODATA_PIXEL_PERCENTAGE": 0,
    }
    values.update(overrides)
    return values


def without(*names):
    return {key: value for key, value in properties().items() if key not in names}


def test_maps_each_source_property_to_its_typed_column():
    columns = scene_metadata_columns(properties())

    assert {key: value for key, value in columns.items() if key != "properties"} == {
        "product_id": "S2B_MSIL1C_20180102T111439_N0500_R137_T29SPA_20230906T202441",
        "spacecraft": "S2B",
        "processing_baseline": "05.00",
        "orbit": 137,
        "acquired_at": "2018-01-02T11:14:39.024Z",
        "cloudy_pixel_percentage": 12.5,
        "cloud_coverage_assessment": 12.25,
        "mean_solar_zenith": 61.817131843863,
        "mean_solar_azimuth": 161.147975508761,
    }


def test_keeps_every_raw_property_as_json():
    raw = properties()

    columns = scene_metadata_columns(raw)

    assert json.loads(columns["properties"]) == raw


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Sentinel-2A", "S2A"), ("Sentinel-2B", "S2B"), ("Sentinel-2C", "S2C")],
)
def test_spacecraft_comes_from_the_spacecraft_name(name, expected):
    assert scene_metadata_columns(properties(SPACECRAFT_NAME=name))["spacecraft"] == expected


def test_spacecraft_falls_back_to_the_product_id_prefix():
    columns = scene_metadata_columns(without("SPACECRAFT_NAME"))

    assert columns["spacecraft"] == "S2B"


def test_spacecraft_name_wins_over_the_product_id():
    columns = scene_metadata_columns(properties(SPACECRAFT_NAME="Sentinel-2A"))

    assert columns["spacecraft"] == "S2A"


def test_an_unrecognised_spacecraft_name_falls_back_to_the_product_id():
    columns = scene_metadata_columns(properties(SPACECRAFT_NAME="Landsat-8"))

    assert columns["spacecraft"] == "S2B"


def test_a_spacecraft_that_cannot_be_determined_is_an_error():
    raw = without("SPACECRAFT_NAME")
    raw["PRODUCT_ID"] = "UNKNOWN_PRODUCT"

    with pytest.raises(ValueError, match="spacecraft"):
        scene_metadata_columns(raw)


@pytest.mark.parametrize("name", ["PRODUCT_ID", "system:time_start"])
def test_a_missing_mandatory_property_is_an_error(name):
    with pytest.raises(ValueError, match=name):
        scene_metadata_columns(without(name))


@pytest.mark.parametrize(
    "name",
    [
        "PROCESSING_BASELINE",
        "SENSING_ORBIT_NUMBER",
        "CLOUDY_PIXEL_PERCENTAGE",
        "CLOUD_COVERAGE_ASSESSMENT",
        "MEAN_SOLAR_ZENITH_ANGLE",
        "MEAN_SOLAR_AZIMUTH_ANGLE",
    ],
)
def test_the_other_properties_are_optional_columns(name):
    columns = scene_metadata_columns(without(name))

    assert None in columns.values()


def test_the_acquisition_time_keeps_the_exact_millisecond():
    columns = scene_metadata_columns(properties(**{"system:time_start": 1514891679001}))

    assert columns["acquired_at"] == "2018-01-02T11:14:39.001Z"


def test_a_future_sentinel_2_unit_is_recognised():
    assert scene_metadata_columns(properties(SPACECRAFT_NAME="Sentinel-2D"))["spacecraft"] == "S2D"
    assert scene_metadata_columns(without("SPACECRAFT_NAME") | {"PRODUCT_ID": "S2D_MSIL1C_x"})["spacecraft"] == "S2D"
