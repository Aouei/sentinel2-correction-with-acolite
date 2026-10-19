from src.steps import describe as describe_module
from src.steps.describe import COLLECTION, describe, fetch_properties

from conftest import GEE_ID

SHORT_ID = "20260921T105031_20260921T105029_T30SXG"
RAW = {"PRODUCT_ID": "S2B_MSIL1C_x", "SPACECRAFT_NAME": "Sentinel-2B"}
TIME_START = 1789987831024


class FakeProperties:
    """Stands in for the ee.Dictionary returned by image.toDictionary()."""

    def __init__(self, data):
        self.data = dict(data)

    def set(self, name, value):
        return FakeProperties({**self.data, name: value})


class FakeImage:
    """Stands in for ee.Image: id, properties and system:time_start."""

    def __init__(self, gee_id=SHORT_ID, properties=RAW, time_start=TIME_START):
        self.gee_id = gee_id
        self.properties = properties
        self.time_start = time_start

    def id(self):
        return self.gee_id

    def toDictionary(self):
        return FakeProperties(self.properties)

    def get(self, name):
        assert name == "system:time_start"
        return self.time_start


class FakeDictionary:
    """Stands in for ee.Dictionary(...): one round trip, counted in `get_info_calls`."""

    get_info_calls = 0

    def __init__(self, mapping):
        self.mapping = mapping

    def getInfo(self):
        FakeDictionary.get_info_calls += 1
        return {
            key: value.data if isinstance(value, FakeProperties) else value
            for key, value in self.mapping.items()
        }


class FakeEE:
    Dictionary = FakeDictionary

    def __init__(self):
        self.images = []

    def Image(self, asset_id):
        self.images.append(asset_id)
        return FakeImage(gee_id=asset_id.rsplit("/", 1)[-1])


def setup_function():
    FakeDictionary.get_info_calls = 0


def use_fake_ee(monkeypatch):
    fake = FakeEE()
    monkeypatch.setattr(describe_module, "ee", fake)
    return fake


def test_describe_returns_the_id_and_the_properties_of_the_image(monkeypatch):
    use_fake_ee(monkeypatch)

    gee_id, properties = describe(FakeImage())

    assert gee_id == SHORT_ID
    assert properties == {**RAW, "system:time_start": TIME_START}


def test_describe_uses_a_single_round_trip(monkeypatch):
    use_fake_ee(monkeypatch)

    describe(FakeImage())

    assert FakeDictionary.get_info_calls == 1


def test_fetch_properties_reads_the_image_from_the_l1c_collection(monkeypatch):
    fake = use_fake_ee(monkeypatch)

    properties = fetch_properties(SHORT_ID)

    assert fake.images == [f"{COLLECTION}/{SHORT_ID}"]
    assert properties == {**RAW, "system:time_start": TIME_START}


def test_fetch_properties_keeps_a_full_asset_id_as_it_is(monkeypatch):
    fake = use_fake_ee(monkeypatch)

    fetch_properties(GEE_ID)

    assert fake.images == [GEE_ID]

