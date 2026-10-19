from datetime import date, timedelta
from typing import Any

import ee
from gee_acolite.utils import search


def extract(date: date, tile: str, roi: dict[str, Any]) -> "ee.Image | None":
    """First Sentinel-2 L1C image of `tile` acquired on `date` inside `roi`, or None."""
    images = search(
        roi=ee.Geometry(roi),
        start=date.isoformat(),
        end=(date + timedelta(days=1)).isoformat(),  # filterDate excludes the end
        tile=tile,
    )
    if images.size().getInfo() == 0:
        return None
    return images.first()
