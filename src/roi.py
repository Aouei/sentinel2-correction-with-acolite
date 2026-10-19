import hashlib
import json
from typing import Any


def hash_roi(roi: dict[str, Any]) -> str:
    """Stable hash of a GeoJSON geometry: the order of the keys does not matter."""
    payload = json.dumps(roi, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
