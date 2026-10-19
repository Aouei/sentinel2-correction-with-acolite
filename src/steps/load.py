from typing import Any

import ee

CRS = "EPSG:4326"
SCALE = 10
MAX_PIXELS = 1e13

# Rrs_B1..Rrs_B7, TUR_B4 (665 nm), chl_oc3, pSDB green and pSDB red.
# Earth Engine is lazy: the bands that are not selected are never computed.
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


def load(image: "ee.Image", asset_id: str, roi: dict[str, Any]) -> "ee.batch.Task":
    """Start the export of the requested bands, clipped to the ROI, to an asset; return the task."""
    selected = image.select(EXPORTED_BANDS)
    task = ee.batch.Export.image.toAsset(
        image=selected,
        description=asset_id.rsplit("/", 1)[-1],
        assetId=asset_id,
        region=ee.Geometry(roi),
        scale=SCALE,
        crs=CRS,
        maxPixels=MAX_PIXELS,
    )
    task.start()
    return task
