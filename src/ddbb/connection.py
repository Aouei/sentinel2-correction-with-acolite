import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from src.roi import hash_roi

if TYPE_CHECKING:
    from src.pipeline import AcoliteConfig

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class DBConnection:
    """Service layer over the SQLite state: each method is a business rule.

    It receives domain keys (date, tile, config), never row ids.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        with self._connect() as connection:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._migrate(connection)

    def register_scene(self, date: date, tile: str, gee_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scenes (date, tile, gee_id) VALUES (?, ?, ?) "
                "ON CONFLICT (date, tile) DO NOTHING",
                (date.isoformat(), tile, gee_id),
            )

    def is_corrected(
        self, date: date, tile: str, config: "AcoliteConfig", roi: dict[str, Any]
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM corrected AS c JOIN scenes AS s ON s.id = c.scene_id "
                "WHERE s.date = ? AND s.tile = ? AND c.ac_version = ? "
                "AND c.params_hash = ? AND c.roi_hash = ? AND c.status = 'corrected'",
                (date.isoformat(), tile, config.version, config.hash(), hash_roi(roi)),
            ).fetchone()
        return row is not None

    def mark_corrected(
        self,
        date: date,
        tile: str,
        config: "AcoliteConfig",
        roi: dict[str, Any],
        asset_id: str,
    ) -> None:
        with self._connect() as connection:
            scene_id = self._require_scene_id(connection, date, tile)
            connection.execute(
                "INSERT INTO corrected (scene_id, ac_version, params_hash, roi_hash, status, asset_id) "
                "VALUES (?, ?, ?, ?, 'corrected', ?) "
                "ON CONFLICT (scene_id, ac_version, params_hash, roi_hash) DO UPDATE SET "
                f"status = 'corrected', asset_id = excluded.asset_id, updated_at = {NOW}",
                (scene_id, config.version, config.hash(), hash_roi(roi), asset_id),
            )

    def mark_error(
        self, date: date, tile: str, config: "AcoliteConfig", roi: dict[str, Any]
    ) -> None:
        with self._connect() as connection:
            scene_id = self._require_scene_id(connection, date, tile)
            connection.execute(
                "INSERT INTO corrected (scene_id, ac_version, params_hash, roi_hash, status) "
                "VALUES (?, ?, ?, ?, 'error') "
                "ON CONFLICT (scene_id, ac_version, params_hash, roi_hash) DO UPDATE SET "
                f"status = 'error', updated_at = {NOW}",
                (scene_id, config.version, config.hash(), hash_roi(roi)),
            )

    def log_error(
        self,
        step: Literal["extract", "transform", "load"],
        message: str,
        date: date,
        tile: str,
        config: "AcoliteConfig | None" = None,
        traceback: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO errors (scene_id, date, tile, ac_version, params_hash, step, error_msg, traceback) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._find_scene_id(connection, date, tile),
                    date.isoformat(),
                    tile,
                    config.version if config else None,
                    config.hash() if config else None,
                    step,
                    message,
                    traceback,
                ),
            )

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """CREATE TABLE IF NOT EXISTS never alters a table: add the columns older databases lack."""
        columns = {row[1] for row in connection.execute("PRAGMA table_info(errors)")}
        if "traceback" not in columns:
            connection.execute("ALTER TABLE errors ADD COLUMN traceback TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:  # commit on success, rollback on error
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _find_scene_id(connection: sqlite3.Connection, date: date, tile: str) -> int | None:
        row = connection.execute(
            "SELECT id FROM scenes WHERE date = ? AND tile = ?", (date.isoformat(), tile)
        ).fetchone()
        return row[0] if row else None

    @classmethod
    def _require_scene_id(cls, connection: sqlite3.Connection, date: date, tile: str) -> int:
        scene_id = cls._find_scene_id(connection, date, tile)
        if scene_id is None:
            raise LookupError(f"Scene not registered: {date.isoformat()} {tile}")
        return scene_id
