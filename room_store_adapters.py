from __future__ import annotations

import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DATABASE_PATH = Path(__file__).with_name("data") / "pointroom.sqlite3"
POSTGRES_SCHEMES = ("postgres://", "postgresql://")
SQLITE_SCHEME = "sqlite://"
POSTGRES_POOL_MAX_SIZE = 10
ROOM_QUERY_BATCH_SIZE = 100


def resolve_dsn() -> str:
    """Resolve an explicit URL, safe PostgreSQL components, or the SQLite path."""
    database_url = os.environ.get("POINTROOM_DATABASE_URL", "").strip()
    if database_url:
        return database_url

    database_host = os.environ.get("POINTROOM_DATABASE_HOST", "").strip()
    if database_host:
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=database_host,
            port=os.environ.get("POINTROOM_DATABASE_PORT", "5432"),
            dbname=os.environ.get("POINTROOM_DATABASE_NAME", "pointroom"),
            user=os.environ.get("POINTROOM_DATABASE_USER", "pointroom"),
            password=os.environ.get("POINTROOM_DATABASE_PASSWORD", ""),
        )

    return os.environ.get("POINTROOM_DATABASE_PATH", str(DEFAULT_DATABASE_PATH))


class RoomStoreAdapter(ABC):
    """Persistence boundary for room snapshots; one implementation per database."""

    name: str
    placeholder: str
    create_table_sql: str
    create_index_sql: str

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @property
    @abstractmethod
    def unavailable_errors(self) -> tuple[type[BaseException], ...]:
        """Exceptions that mean the database is unreachable rather than misused."""

    @abstractmethod
    def connection(self) -> Any:
        """A context manager yielding a connection that commits on success."""

    def close(self) -> None:
        return None

    def sql(self, statement: str) -> str:
        return statement.replace("?", self.placeholder)

    def initialize(self) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self.create_table_sql)
            cursor.execute(self.create_index_sql)

    def ping(self) -> bool:
        try:
            with self.connection() as connection:
                connection.cursor().execute("SELECT 1")
        except self.unavailable_errors:
            return False
        return True

    def delete_expired(self, cutoff: float) -> int:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql("DELETE FROM rooms WHERE updated_at < ?"), (cutoff,)
            )
            return max(cursor.rowcount, 0)

    def fetch_states(self) -> list[str]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT state_json FROM rooms")
            return [row[0] for row in cursor.fetchall()]

    def fetch_state_page(
        self,
        after_room_id: str | None = None,
        limit: int = ROOM_QUERY_BATCH_SIZE,
    ) -> list[tuple[str, float, str]]:
        page_size = max(1, int(limit))
        if after_room_id is None:
            statement = self.sql(
                "SELECT room_id, updated_at, state_json FROM rooms "
                "ORDER BY room_id LIMIT ?"
            )
            parameters = (page_size,)
        else:
            statement = self.sql(
                "SELECT room_id, updated_at, state_json FROM rooms "
                "WHERE room_id > ? ORDER BY room_id LIMIT ?"
            )
            parameters = (after_room_id, page_size)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(statement, parameters)
            return cursor.fetchall()

    def has_legacy_snapshots(self) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql(
                    "SELECT 1 FROM rooms "
                    "WHERE state_json LIKE ? LIMIT 1"
                ),
                ('%"host_token":%',),
            )
            return cursor.fetchone() is not None

    def fetch_room_ids(self) -> set[str]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT room_id FROM rooms")
            return {row[0] for row in cursor.fetchall()}

    def fetch_expired_room_ids(
        self,
        cutoff: float,
        limit: int = ROOM_QUERY_BATCH_SIZE,
    ) -> set[str]:
        batch_size = max(1, int(limit))
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql(
                    "SELECT room_id FROM rooms "
                    "WHERE updated_at < ? ORDER BY updated_at, room_id LIMIT ?"
                ),
                (cutoff, batch_size),
            )
            return {row[0] for row in cursor.fetchall()}

    def delete_expired_room_ids(
        self,
        cutoff: float,
        room_ids: set[str],
    ) -> set[str]:
        if not room_ids:
            return set()
        placeholders = ", ".join("?" for _ in room_ids)
        parameters = (cutoff, *room_ids)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql(
                    "SELECT room_id FROM rooms "
                    f"WHERE updated_at < ? AND room_id IN ({placeholders})"
                ),
                parameters,
            )
            expired_room_ids = {row[0] for row in cursor.fetchall()}
            if expired_room_ids:
                delete_placeholders = ", ".join(
                    "?" for _ in expired_room_ids
                )
                cursor.execute(
                    self.sql(
                        "DELETE FROM rooms "
                        f"WHERE room_id IN ({delete_placeholders})"
                    ),
                    tuple(expired_room_ids),
                )
            return expired_room_ids

    def count_rooms(self) -> int:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM rooms")
            return int(cursor.fetchone()[0])

    def has_room_id(self, room_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql("SELECT 1 FROM rooms WHERE room_id = ? LIMIT 1"),
                (room_id,),
            )
            return cursor.fetchone() is not None

    def fetch_state(self, room_id: str) -> str | None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                self.sql("SELECT state_json FROM rooms WHERE room_id = ?"),
                (room_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def delete_room(self, room_id: str) -> None:
        with self.connection() as connection:
            connection.cursor().execute(
                self.sql("DELETE FROM rooms WHERE room_id = ?"), (room_id,)
            )

    def upsert_rooms(self, records: list[tuple[str, float, str]]) -> None:
        upsert = self.sql(
            """
            INSERT INTO rooms (room_id, updated_at, state_json)
            VALUES (?, ?, ?)
            ON CONFLICT (room_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                state_json = excluded.state_json
            """
        )
        with self.connection() as connection:
            cursor = connection.cursor()
            for record in records:
                cursor.execute(upsert, record)


class SqliteAdapter(RoomStoreAdapter):
    name = "sqlite"
    placeholder = "?"
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS rooms (
            room_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            state_json TEXT NOT NULL
        )
    """
    create_index_sql = "CREATE INDEX IF NOT EXISTS idx_rooms_updated_at ON rooms (updated_at)"

    def __init__(self, dsn: str) -> None:
        path = dsn[len(SQLITE_SCHEME) :] if dsn.startswith(SQLITE_SCHEME) else dsn
        super().__init__(os.path.abspath(path))
        parent = os.path.dirname(self.dsn)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @property
    def unavailable_errors(self) -> tuple[type[BaseException], ...]:
        return (OSError, sqlite3.Error)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        connection = sqlite3.connect(self.dsn, timeout=5)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgresAdapter(RoomStoreAdapter):
    name = "postgres"
    placeholder = "%s"
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS rooms (
            room_id text PRIMARY KEY,
            updated_at double precision NOT NULL,
            state_json text NOT NULL
        )
    """
    create_index_sql = "CREATE INDEX IF NOT EXISTS idx_rooms_updated_at ON rooms (updated_at)"

    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        super().__init__(dsn)
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=POSTGRES_POOL_MAX_SIZE, open=True
        )

    @property
    def unavailable_errors(self) -> tuple[type[BaseException], ...]:
        import psycopg

        return (OSError, psycopg.Error)

    def connection(self) -> Any:
        return self._pool.connection()

    def close(self) -> None:
        self._pool.close()


def is_postgres_dsn(dsn: str) -> bool:
    normalized_dsn = dsn.lstrip()
    return normalized_dsn.startswith(POSTGRES_SCHEMES) or normalized_dsn.startswith(
        ("host=", "hostaddr=", "dbname=", "user=")
    )


def adapter_class_for_dsn(dsn: str) -> type[RoomStoreAdapter]:
    return PostgresAdapter if is_postgres_dsn(dsn) else SqliteAdapter


def create_adapter(dsn: str) -> RoomStoreAdapter:
    return adapter_class_for_dsn(dsn)(dsn)
