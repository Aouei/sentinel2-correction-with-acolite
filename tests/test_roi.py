import copy

from src.roi import hash_roi

from conftest import OTHER_ROI, ROI


def test_hash_is_a_non_empty_string():
    assert isinstance(hash_roi(ROI), str)
    assert hash_roi(ROI) != ""


def test_hash_is_deterministic():
    assert hash_roi(ROI) == hash_roi(copy.deepcopy(ROI))


def test_hash_ignores_the_order_of_the_keys():
    reordered = {"coordinates": ROI["coordinates"], "type": ROI["type"]}

    assert hash_roi(reordered) == hash_roi(ROI)


def test_different_geometries_have_different_hashes():
    assert hash_roi(ROI) != hash_roi(OTHER_ROI)


def test_hash_changes_when_a_coordinate_changes():
    moved = copy.deepcopy(ROI)
    moved["coordinates"][0][0][0] += 0.001

    assert hash_roi(moved) != hash_roi(ROI)
