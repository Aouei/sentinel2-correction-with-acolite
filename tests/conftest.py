import sqlite3
from datetime import date

import pytest

from src.ddbb.connection import DBConnection
from src.pipeline import AcoliteConfig

DAY = date(2026, 9, 21)
TILE = "30SXG"
GEE_ID = "COPERNICUS/S2_HARMONIZED/20260921T105031_20260921T105029_T30SXG"

# GeoJSON geometries (what the CLI reads from roi.json and hands to the pipeline).
ROI = {
    "type": "Polygon",
    "coordinates": [
        [
            [-7.8838, 37.0335],
            [-7.7368, 37.0335],
            [-7.7368, 36.9519],
            [-7.8838, 36.9519],
            [-7.8838, 37.0335],
        ]
    ],
}
OTHER_ROI = {
    "type": "Polygon",
    "coordinates": [
        [
            [-0.90, 37.80],
            [-0.70, 37.80],
            [-0.70, 37.65],
            [-0.90, 37.65],
            [-0.90, 37.80],
        ]
    ],
}


@pytest.fixture
def config(tmp_path):
    return AcoliteConfig(
        version="20231023.0",
        path=tmp_path / "acolite",
        params={"l2w_parameters": "Rrs_*,pSDB_green", "s2_target_res": 10},
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "state.db"


@pytest.fixture
def db(db_path):
    return DBConnection(db_path)


@pytest.fixture
def raw_db(db, db_path):
    """Plain sqlite3 connection used only to inspect what DBConnection wrote."""
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()
