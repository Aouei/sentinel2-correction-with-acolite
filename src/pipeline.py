import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from src.ddbb.connection import DBConnection
from src.roi import hash_roi

ASSET_FOLDER = "projects/ee-sergiohercar1/assets/ETL"
ASSET_HASH_LENGTH = 12  # the full hashes live in the database; the asset name stays short
POLL_SECONDS = 10
CREDENTIAL_KEYS = frozenset({"EARTHDATA_u", "EARTHDATA_p"})


@dataclass(frozen=True)
class AcoliteConfig:
    version: str
    path: Path
    params: dict[str, Any]

    def hash(self) -> str:
        """Stable hash of version + params. Ignores the path and the credentials."""
        params = {key: value for key, value in self.params.items() if key not in CREDENTIAL_KEYS}
        payload = json.dumps({"version": self.version, "params": params}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RunStatus(Enum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"


class ExportError(Exception):
    """The Earth Engine export task did not complete."""


class Pipeline:
    def __init__(
        self,
        extract: Callable[[date, str, dict[str, Any]], Any],
        transform: Callable[[Any, AcoliteConfig], Any],
        load: Callable[[Any, str, dict[str, Any]], Any],
        db: DBConnection,
    ) -> None:
        self._extract = extract
        self._transform = transform
        self._load = load
        self._db = db

    def run(
        self, date: date, tile: str, config: AcoliteConfig, roi: dict[str, Any]
    ) -> RunStatus:
        if self._db.is_corrected(date, tile, config, roi):
            return RunStatus.SUCCESS

        try:
            image = self._extract(date, tile, roi)
            gee_id = image.id().getInfo() if image is not None else None
        except Exception as error:
            self._db.log_error("extract", _describe(error), date, tile)
            return RunStatus.FAIL

        if image is None:
            return RunStatus.SUCCESS

        self._db.register_scene(date, tile, gee_id)

        try:
            corrected = self._transform(image, config)
        except Exception as error:
            return self._fail("transform", error, date, tile, config, roi)

        asset_id = _asset_id(date, tile, config, roi)
        try:
            task = self._load(corrected, asset_id, roi)
            _wait_until_completed(task)
        except Exception as error:
            return self._fail("load", error, date, tile, config, roi)

        self._db.mark_corrected(date, tile, config, roi, asset_id)
        return RunStatus.SUCCESS

    def _fail(
        self,
        step: Literal["transform", "load"],
        error: Exception,
        date: date,
        tile: str,
        config: AcoliteConfig,
        roi: dict[str, Any],
    ) -> RunStatus:
        self._db.log_error(step, _describe(error), date, tile, config)
        self._db.mark_error(date, tile, config, roi)
        return RunStatus.FAIL


def _asset_id(date: date, tile: str, config: AcoliteConfig, roi: dict[str, Any]) -> str:
    config_hash = config.hash()[:ASSET_HASH_LENGTH]
    roi_hash = hash_roi(roi)[:ASSET_HASH_LENGTH]
    return f"{ASSET_FOLDER}/{date.isoformat()}_{tile}_{config_hash}_{roi_hash}_L2W"


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _wait_until_completed(task: Any) -> None:
    while True:
        status = task.status()
        state = status["state"]
        if state == "COMPLETED":
            return
        if state in ("FAILED", "CANCELLED"):
            raise ExportError(status.get("error_message") or state)
        time.sleep(POLL_SECONDS)
