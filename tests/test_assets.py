from types import SimpleNamespace

import pytest

from src.steps import assets as assets_module
from src.steps.assets import asset_exists

ASSET_ID = "projects/ee-sergiohercar1/assets/ETL/2018-09-19_T29SPA_abcf7e790374_938f06a7a4a5_L2W"


class FakeEEException(Exception):
    pass


def fake_earth_engine(monkeypatch, get_asset):
    fake_ee = SimpleNamespace(EEException=FakeEEException, data=SimpleNamespace(getAsset=get_asset))
    monkeypatch.setattr(assets_module, "ee", fake_ee)


def test_returns_true_when_the_asset_exists(monkeypatch):
    requested = []
    fake_earth_engine(monkeypatch, lambda asset_id: requested.append(asset_id) or {"type": "IMAGE"})

    assert asset_exists(ASSET_ID) is True
    assert requested == [ASSET_ID]


def test_returns_false_when_earth_engine_says_it_does_not_exist(monkeypatch):
    def missing(asset_id):
        raise FakeEEException(f"Asset '{asset_id}' does not exist or doesn't allow this operation.")

    fake_earth_engine(monkeypatch, missing)

    assert asset_exists(ASSET_ID) is False


def test_other_earth_engine_errors_are_not_swallowed(monkeypatch):
    def broken(asset_id):
        raise FakeEEException("Quota exceeded")

    fake_earth_engine(monkeypatch, broken)

    with pytest.raises(FakeEEException, match="Quota exceeded"):
        asset_exists(ASSET_ID)


def test_network_errors_are_not_swallowed(monkeypatch):
    def offline(asset_id):
        raise ConnectionError("offline")

    fake_earth_engine(monkeypatch, offline)

    with pytest.raises(ConnectionError):
        asset_exists(ASSET_ID)
