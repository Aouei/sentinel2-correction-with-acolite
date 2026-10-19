import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from src.roi import hash_roi
from src.scene_metadata import scene_metadata_columns

if TYPE_CHECKING:
    from src.pipeline import AcoliteConfig

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _short_id(gee_id: str) -> str:
    """The database keeps the short system:index; Earth Engine also reports `COLLECTION/index`."""
    return gee_id.rsplit("/", 1)[-1]


class DBConnection:
    """Service layer over the SQLite state: each method is a business rule.

    It receives domain keys (date, tile, config), never row ids.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        with self._connect() as connection:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._migrate(connection)

    def register_scene(self, date: date, tile: str, gee_id: str | None) -> None:
        """Record that (date, tile) was searched. gee_id is None when Earth Engine has no image.

        Idempotent. A NULL gee_id is filled if an image shows up later, but a known gee_id is
        never replaced (by NULL or by another id).
        """
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scenes (date, tile, gee_id) VALUES (?, ?, ?) "
                "ON CONFLICT (date, tile) DO UPDATE SET gee_id = excluded.gee_id "
                "WHERE scenes.gee_id IS NULL AND excluded.gee_id IS NOT NULL",
                (date.isoformat(), tile, gee_id),
            )

    def save_scene_metadata(
        self, date: date, tile: str, properties: dict[str, Any], *, gee_id: str
    ) -> None:
        """Store the Sentinel-2 L1C image properties of a registered scene (typed columns + raw JSON).

        `gee_id` is the id of the image the properties belong to: it must be the one registered for
        the scene, because `register_scene` never replaces a known id and the metadata must not
        describe another image. The short and the full (`COPERNICUS/S2_HARMONIZED/...`) forms match.

        Idempotent: saving again refreshes the row. Raises ValueError when the properties lack a
        mandatory field or the image is not the registered one (nothing is written) and
        LookupError when the scene is not registered.
        """
        columns = scene_metadata_columns(properties)
        names = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        refresh = ", ".join(f"{name} = excluded.{name}" for name in columns)
        with self._connect() as connection:
            scene_id = self._require_scene_id(connection, date, tile)
            (registered,) = connection.execute("SELECT gee_id FROM scenes WHERE id = ?", (scene_id,)).fetchone()
            if registered is None or _short_id(registered) != _short_id(gee_id):
                raise ValueError(
                    f"the metadata describes image {gee_id}, but {date.isoformat()} {tile} "
                    f"is registered with image {registered}"
                )
            connection.execute(
                f"INSERT INTO scene_metadata (scene_id, {names}) VALUES (?, {marks}) "
                f"ON CONFLICT (scene_id) DO UPDATE SET {refresh}",
                (scene_id, *columns.values()),
            )

    def scenes_without_metadata(self) -> list[tuple[date, str, str]]:
        """(date, tile, gee_id) of every scene with an image and no scene_metadata row, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.date, s.tile, s.gee_id FROM scenes AS s "
                "LEFT JOIN scene_metadata AS m ON m.scene_id = s.id "
                "WHERE s.gee_id IS NOT NULL AND m.scene_id IS NULL ORDER BY s.date, s.tile"
            ).fetchall()
        return [(date.fromisoformat(day), tile, gee_id) for day, tile, gee_id in rows]

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

    @classmethod
    def _migrate(cls, connection: sqlite3.Connection) -> None:
        """CREATE TABLE IF NOT EXISTS never alters a table: bring older databases up to date."""
        columns = {row[1] for row in connection.execute("PRAGMA table_info(errors)")}
        if "traceback" not in columns:
            connection.execute("ALTER TABLE errors ADD COLUMN traceback TEXT")
        cls._make_gee_id_nullable(connection)

    @staticmethod
    def _make_gee_id_nullable(connection: sqlite3.Connection) -> None:
        """SQLite cannot drop a NOT NULL: rebuild `scenes` as SQLite documents (foreign keys off)."""
        columns = list(connection.execute("PRAGMA table_info(scenes)"))
        gee_id = next(row for row in columns if row[1] == "gee_id")
        if gee_id[3] == 0:  # already nullable
            return

        # The new DDL comes from schema.sql itself, so it is never duplicated here.
        fresh = sqlite3.connect(":memory:")
        fresh.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        scenes_sql = fresh.execute("SELECT sql FROM sqlite_master WHERE name = 'scenes'").fetchone()[0]
        fresh.close()

        names = ", ".join(row[1] for row in columns)
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                "BEGIN;"
                + scenes_sql.replace("CREATE TABLE scenes", "CREATE TABLE scenes_new", 1)
                + f"; INSERT INTO scenes_new ({names}) SELECT {names} FROM scenes;"
                + "DROP TABLE scenes;"
                + "ALTER TABLE scenes_new RENAME TO scenes;"
                + "COMMIT;"
            )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

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
