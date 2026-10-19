import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SPACECRAFT_NAME_PATTERN = re.compile(r"Sentinel-2([A-Z])")
PRODUCT_ID_PATTERN = re.compile(r"S2([A-Z])_")


def scene_metadata_columns(properties: dict[str, Any]) -> dict[str, Any]:
    """Typed `scene_metadata` columns of the properties of a Sentinel-2 L1C image.

    Shared by the pipeline and the backfill script. PRODUCT_ID, the acquisition time and the
    spacecraft are mandatory (ValueError when missing); the rest is NULL when absent.
    """
    return {
        "product_id": _required(properties, "PRODUCT_ID"),
        "spacecraft": _spacecraft(properties),
        "processing_baseline": properties.get("PROCESSING_BASELINE"),
        "orbit": properties.get("SENSING_ORBIT_NUMBER"),
        "acquired_at": _acquired_at(_required(properties, "system:time_start")),
        "cloudy_pixel_percentage": properties.get("CLOUDY_PIXEL_PERCENTAGE"),
        "cloud_coverage_assessment": properties.get("CLOUD_COVERAGE_ASSESSMENT"),
        "mean_solar_zenith": properties.get("MEAN_SOLAR_ZENITH_ANGLE"),
        "mean_solar_azimuth": properties.get("MEAN_SOLAR_AZIMUTH_ANGLE"),
        "properties": json.dumps(properties, sort_keys=True),
    }


def _required(properties: dict[str, Any], name: str) -> Any:
    value = properties.get(name)
    if value is None:
        raise ValueError(f"the image has no {name} property")
    return value


def _spacecraft(properties: dict[str, Any]) -> str:
    """S2A, S2B, S2C... from SPACECRAFT_NAME, falling back to the PRODUCT_ID prefix."""
    name_match = SPACECRAFT_NAME_PATTERN.search(str(properties.get("SPACECRAFT_NAME", "")))
    if name_match:
        return f"S2{name_match.group(1)}"
    product_match = PRODUCT_ID_PATTERN.match(str(properties.get("PRODUCT_ID", "")))
    if product_match:
        return f"S2{product_match.group(1)}"
    raise ValueError("cannot tell the spacecraft from SPACECRAFT_NAME or PRODUCT_ID")


def _acquired_at(time_start_ms: int) -> str:
    """UTC ISO-8601 text with milliseconds, like every other timestamp in the database."""
    moment = EPOCH + timedelta(milliseconds=time_start_ms)  # integer maths: no float rounding
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"
