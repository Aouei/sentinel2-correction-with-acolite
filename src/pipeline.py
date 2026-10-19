import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from traceback import format_exception
from typing import Any, Literal

from src.ddbb.connection import DBConnection
from src.roi import hash_roi

ASSET_FOLDER = "projects/ee-sergiohercar1/assets/ETL"
ASSET_HASH_LENGTH = 12  # the full hashes live in the database; the asset name stays short
POLL_SECONDS = 10
TASK_TIMEOUT_SECONDS = 30 * 60  # a stuck export (e.g. READY in a saturated queue) must not block a batch
MAX_STATUS_ERRORS = 5  # consecutive failed reads of the task status before giving up
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
        asset_exists: Callable[[str], bool],
        db: DBConnection,
    ) -> None:
        self._extract = extract
        self._transform = transform
        self._load = load
        self._asset_exists = asset_exists
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
            self._db.log_error("extract", _describe(error), date, tile, traceback=_trace(error))
            return RunStatus.FAIL

        if image is None:
            return RunStatus.SUCCESS

        self._db.register_scene(date, tile, gee_id)

        # The asset id is deterministic and Earth Engine never overwrites: if a previous run lost
        # its export (crash, Ctrl-C, locked database) the asset exists anyway, so adopt it.
        asset_id = _asset_id(date, tile, config, roi)
        try:
            already_exported = self._asset_exists(asset_id)
        except Exception as error:
            return self._fail("load", error, date, tile, config, roi)
        if already_exported:
            self._db.mark_corrected(date, tile, config, roi, asset_id)
            return RunStatus.SUCCESS

        try:
            corrected = self._transform(image, config)
        except Exception as error:
            return self._fail("transform", error, date, tile, config, roi)

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
        self._db.log_error(step, _describe(error), date, tile, config, traceback=_trace(error))
        self._db.mark_error(date, tile, config, roi)
        return RunStatus.FAIL


def _asset_id(date: date, tile: str, config: AcoliteConfig, roi: dict[str, Any]) -> str:
    config_hash = config.hash()[:ASSET_HASH_LENGTH]
    roi_hash = hash_roi(roi)[:ASSET_HASH_LENGTH]
    return f"{ASSET_FOLDER}/{date.isoformat()}_{tile}_{config_hash}_{roi_hash}_L2W"


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _trace(error: Exception) -> str:
    return "".join(format_exception(error))


def _wait_until_completed(task: Any) -> None:
    """Poll the export task. Tolerates transient read errors, times out, cancels on Ctrl-C."""
    waited = 0
    failed_reads = 0
    try:
        while True:
            try:
                status = task.status()
            except Exception as error:
                failed_reads += 1
                if failed_reads >= MAX_STATUS_ERRORS:
                    raise ExportError(
                        f"could not read the task status {failed_reads} times in a row: {_describe(error)}"
                    ) from error
            else:
                failed_reads = 0
                state = status["state"]
                if state == "COMPLETED":
                    return
                if state in ("FAILED", "CANCELLED"):
                    raise ExportError(status.get("error_message") or state)

            if waited >= TASK_TIMEOUT_SECONDS:
                _cancel_quietly(task)
                raise ExportError(f"timed out after {waited} s waiting for the export task")
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
    except KeyboardInterrupt:
        # Do not leave an orphan export that would create the asset behind our back.
        _cancel_quietly(task)
        raise


def _cancel_quietly(task: Any) -> None:
    try:
        task.cancel()
    except Exception:
        pass
