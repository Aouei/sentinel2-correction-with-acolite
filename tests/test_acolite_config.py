from pathlib import Path

from src.pipeline import AcoliteConfig


def make_config(version="20231023.0", path=Path("acolite"), **params):
    return AcoliteConfig(version=version, path=path, params=params)


def test_hash_is_a_non_empty_string(config):
    assert isinstance(config.hash(), str)
    assert config.hash() != ""


def test_hash_is_deterministic():
    assert make_config(a=1, b=2).hash() == make_config(a=1, b=2).hash()


def test_hash_ignores_the_order_of_the_params():
    first = make_config(a=1, b=2)
    second = make_config(b=2, a=1)

    assert first.hash() == second.hash()


def test_hash_ignores_the_order_of_nested_params():
    first = make_config(nested={"a": 1, "b": 2})
    second = make_config(nested={"b": 2, "a": 1})

    assert first.hash() == second.hash()


def test_hash_changes_when_the_version_changes():
    assert make_config(version="20231023.0", a=1).hash() != make_config(version="20240101.0", a=1).hash()


def test_hash_changes_when_a_param_value_changes():
    assert make_config(s2_target_res=10).hash() != make_config(s2_target_res=20).hash()


def test_hash_changes_when_a_param_is_added():
    assert make_config(a=1).hash() != make_config(a=1, b=2).hash()


def test_hash_ignores_the_acolite_path():
    first = make_config(path=Path("C:/one/acolite"), a=1)
    second = make_config(path=Path("D:/other/acolite"), a=1)

    assert first.hash() == second.hash()


def test_hash_ignores_the_earthdata_credentials():
    without = make_config(a=1)
    with_credentials = make_config(a=1, EARTHDATA_u="user", EARTHDATA_p="secret")
    other_credentials = make_config(a=1, EARTHDATA_u="someone", EARTHDATA_p="else")

    assert without.hash() == with_credentials.hash() == other_credentials.hash()


def test_hash_does_not_remove_the_credentials_from_the_config():
    config = make_config(a=1, EARTHDATA_u="user", EARTHDATA_p="secret")

    config.hash()

    assert config.params["EARTHDATA_u"] == "user"
    assert config.params["EARTHDATA_p"] == "secret"
