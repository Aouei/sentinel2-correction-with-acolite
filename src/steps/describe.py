from typing import Any

import ee

COLLECTION = "COPERNICUS/S2_HARMONIZED"  # the collection `extract` searches
TIME_START = "system:time_start"


def describe(image: "ee.Image") -> tuple[str, dict[str, Any]]:
    """(gee_id, properties) of a Sentinel-2 L1C image, in a single Earth Engine round trip.

    toDictionary() leaves out the `system:*` properties, so the acquisition time is added by hand.
    """
    properties = image.toDictionary().set(TIME_START, image.get(TIME_START))
    info = ee.Dictionary({"id": image.id(), "properties": properties}).getInfo()
    return info["id"], info["properties"]


def image_asset_id(gee_id: str) -> str:
    """Full Earth Engine asset id of an image; the database keeps the short system:index."""
    return gee_id if "/" in gee_id else f"{COLLECTION}/{gee_id}"


def fetch_properties(gee_id: str) -> dict[str, Any]:
    """Properties of an image already known by its gee_id. Used to backfill older scenes."""
    return describe(ee.Image(image_asset_id(gee_id)))[1]
