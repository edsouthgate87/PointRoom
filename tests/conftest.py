import os
import pytest

from app import app, limiter

store = app.extensions["room_store"]
event_rate_limiter = app.extensions["event_rate_limiter"]
socket_connection_limiter = app.extensions["socket_connection_limiter"]

TEST_DATABASE_URL = os.environ.get("POINTROOM_TEST_DATABASE_URL", "").strip()


@pytest.fixture(autouse=True)
def clear_rooms(dsn_factory):
    original_testing = app.config["TESTING"]
    original_require_https = app.config["REQUIRE_HTTPS"]
    original_database_dsn = app.config["DATABASE_DSN"]
    original_retention_days = app.config["ROOM_RETENTION_DAYS"]
    original_rate_limiting = app.config["RATELIMIT_ENABLED"]
    original_limiter_enabled = limiter.enabled
    app.config["TESTING"] = True
    app.config["REQUIRE_HTTPS"] = False
    app.config["RATELIMIT_ENABLED"] = False
    limiter.enabled = False
    test_dsn = dsn_factory()
    app.config["DATABASE_DSN"] = test_dsn
    app.config["ROOM_RETENTION_DAYS"] = 30
    store.configure(test_dsn, 30)
    event_rate_limiter.clear()
    socket_connection_limiter.clear()
    with store.lock:
        store.rooms.clear()
        store.connections.clear()
    yield
    with store.lock:
        store.rooms.clear()
        store.connections.clear()
    event_rate_limiter.clear()
    socket_connection_limiter.clear()
    app.config["TESTING"] = original_testing
    app.config["REQUIRE_HTTPS"] = original_require_https
    app.config["RATELIMIT_ENABLED"] = original_rate_limiting
    limiter.enabled = original_limiter_enabled
    app.config["DATABASE_DSN"] = original_database_dsn
    app.config["ROOM_RETENTION_DAYS"] = original_retention_days


@pytest.fixture
def dsn_factory(tmp_path):
    """Builds isolated DSNs so every test runs against either backend unchanged."""

    def make(name="pointroom"):
        if not TEST_DATABASE_URL:
            return str(tmp_path / f"{name}.sqlite3")

        import psycopg

        schema = f"test_{name}".replace("-", "_").replace(".", "_")
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.execute(f'CREATE SCHEMA "{schema}"')
        separator = "&" if "?" in TEST_DATABASE_URL else "?"
        return f"{TEST_DATABASE_URL}{separator}options=-csearch_path%3D{schema}"

    return make


@pytest.fixture
def raw_sql():
    """Runs a statement against a store's database, in that adapter's dialect."""

    def run(store, statement, params=()):
        with store.adapter.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(store.adapter.sql(statement), params)
            return cursor.fetchall() if cursor.description else []

    return run
