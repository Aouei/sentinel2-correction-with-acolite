import sys

import pytest

from src.pipeline import AcoliteConfig
from src.steps import transform as transform_module
from src.steps.transform import transform

from fakes import FakeCollection, FakeImage


class FakeImages:
    """What `ee.ImageCollection.fromImages(...)` returns in these tests."""

    def __init__(self, images):
        self.images = list(images)


class FakeImageCollection:
    @staticmethod
    def fromImages(images):
        return FakeImages(images)


class FakeEE:
    ImageCollection = FakeImageCollection


@pytest.fixture
def acolite_home(tmp_path):
    """A folder that contains an `acolite` package, like data/acolite-20231023.0."""
    package = tmp_path / "acolite-home" / "acolite"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('MARKER = "fake acolite"\n', encoding="utf-8")
    return tmp_path / "acolite-home"


@pytest.fixture(autouse=True)
def isolate_imports(monkeypatch):
    """transform mutates sys.path and imports `acolite`: undo both after every test."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    sys.modules.pop("acolite", None)


@pytest.fixture
def corrector(monkeypatch):
    """Replaces gee_acolite's ACOLITE; returns the list of instances that were built."""
    created = []

    class FakeACOLITE:
        def __init__(self, acolite, settings):
            self.acolite = acolite
            self.settings = settings
            self.corrected = FakeImage("corrected")
            self.received = None
            created.append(self)

        def correct(self, images):
            self.received = images
            self.settings["uoz"] = 0.3  # the real class mutates the settings it is given
            return FakeCollection([self.corrected]), self.settings

    monkeypatch.setattr(transform_module, "ee", FakeEE)
    monkeypatch.setattr(transform_module, "ACOLITE", FakeACOLITE)
    return created


@pytest.fixture
def acolite_config(acolite_home):
    return AcoliteConfig(
        version="20231023.0",
        path=acolite_home,
        params={"l2w_parameters": "Rrs_*", "EARTHDATA_u": "user", "EARTHDATA_p": "secret"},
    )


def test_adds_the_acolite_folder_to_sys_path(corrector, acolite_config):
    transform(FakeImage(), acolite_config)

    assert str(acolite_config.path) in sys.path


def test_imports_acolite_from_that_folder(corrector, acolite_config):
    transform(FakeImage(), acolite_config)

    assert corrector[0].acolite.MARKER == "fake acolite"


def test_does_not_add_the_folder_twice(corrector, acolite_config):
    transform(FakeImage(), acolite_config)
    transform(FakeImage(), acolite_config)

    assert sys.path.count(str(acolite_config.path)) == 1


def test_builds_the_corrector_with_the_params(corrector, acolite_config):
    transform(FakeImage(), acolite_config)

    assert {key: value for key, value in corrector[0].settings.items() if key != "uoz"} == acolite_config.params


def test_passes_a_copy_of_the_params_not_the_config_dict(corrector, acolite_config):
    transform(FakeImage(), acolite_config)

    assert corrector[0].settings is not acolite_config.params


def test_keeps_the_credentials_in_the_params_given_to_acolite(corrector, acolite_config):
    transform(FakeImage(), acolite_config)

    assert corrector[0].settings["EARTHDATA_u"] == "user"
    assert corrector[0].settings["EARTHDATA_p"] == "secret"


def test_does_not_mutate_the_config_params_so_the_hash_stays_stable(corrector, acolite_config):
    before = acolite_config.hash()

    transform(FakeImage(), acolite_config)

    assert "uoz" not in acolite_config.params
    assert acolite_config.hash() == before


def test_corrects_a_collection_that_contains_the_image(corrector, acolite_config):
    image = FakeImage("toa")

    transform(image, acolite_config)

    assert corrector[0].received.images == [image]


def test_returns_the_first_corrected_image(corrector, acolite_config):
    assert transform(FakeImage(), acolite_config) is corrector[0].corrected


def test_acolite_errors_propagate(monkeypatch, acolite_config):
    class BrokenACOLITE:
        def __init__(self, acolite, settings):
            pass

        def correct(self, images):
            raise ValueError("acolite crashed")

    monkeypatch.setattr(transform_module, "ee", FakeEE)
    monkeypatch.setattr(transform_module, "ACOLITE", BrokenACOLITE)

    with pytest.raises(ValueError, match="acolite crashed"):
        transform(FakeImage(), acolite_config)
