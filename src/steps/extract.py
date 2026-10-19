from datetime import date, timedelta
from typing import Any

import ee
from gee_acolite.utils import search


def extract(date: date, tile: str, roi: dict[str, Any]) -> "ee.Image | None":
    """Sentinel-2 L1C image of `tile` acquired on `date` inside `roi`, or None.

    A day normally has one image, but Earth Engine can hold two products of the same acquisition
    (e.g. 2018-04-12 on T29SPA, generated minutes apart): the most recently generated one wins.
    """
    images = search(
        roi=ee.Geometry(roi),
        start=date.isoformat(),
        end=(date + timedelta(days=1)).isoformat(),  # filterDate excludes the end
        tile=tile,
    )
    if images.size().getInfo() == 0:
        return None
    return images.sort("GENERATION_TIME", False).first()
