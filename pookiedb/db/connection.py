import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urlparse

from pookiedb.exceptions import ConnectionError as PookieConnectionError

_connections: dict[str, "_ConnectionPool"] = {}
_default_alias = "default"
_thread_local = threading.local()


class DatabaseConfig:
    """Holds parsed configuration for a single database."""

    def __init__(self, url: str = None, **kwargs):
        if url:
            self._parse_url(url)
        else:
            self.engine = kwargs.get("engine", "sqlite")
            self.name = kwargs.get("name", ":memory:")
            self.host = kwargs.get("host", "localhost")
            self.port = kwargs.get("port", 5432)
            self.user = kwargs.get("user", "")
            self.password = kwargs.get("password", "")

    def _parse_url(self, url: str):
        # Special-case the common sqlite://:memory: shorthand
        if url in ("sqlite://:memory:", "sqlite:///:memory:"):
            self.engine = "sqlite"
            self.name = ":memory:"
            self.host = "localhost"
            self.port = 5432
            self.user = ""
            self.password = ""
            return

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme in ("postgres", "postgresql", "psql"):
            self.engine = "postgresql"
        elif scheme == "sqlite":
            self.engine = "sqlite"
        else:
            raise PookieConnectionError(f"Unsupported database scheme: {scheme}")

        if self.engine == "sqlite":
            # sqlite:///relative.db → "relative.db", sqlite:////abs/path.db → "/abs/path.db"
            self.name = parsed.path[1:] or ":memory:"
        else:
            self.name = parsed.path.lstrip("/") or ":memory:"
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 5432
        self.user = parsed.username or ""
        self.password = parsed.password or ""

    @property
    def is_postgres(self):
        return self.engine == "postgresql"

    @property
    def is_sqlite(self):
        return self.engine == "sqlite"


class _ConnectionPool:
    """Simple thread-safe connection pool."""

    def __init__(self, config: DatabaseConfig, max_connections: int = 10):
        self.config = config
        self.max_connections = max_connections
        self._lock = threading.Lock()
        self._connections: list = []
        self._in_use: set = set()

    def _make_connection(self):
        cfg = self.config
        if cfg.is_sqlite:
            conn = sqlite3.connect(cfg.name, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        else:
            try:
                import psycopg2
                import psycopg2.extras
            except ImportError:
                raise PookieConnectionError(
                    "psycopg2 is required for PostgreSQL. Install it with: pip install psycopg2-binary"
                )
            conn = psycopg2.connect(
                dbname=cfg.name,
                host=cfg.host,
                port=cfg.port,
                user=cfg.user,
                password=cfg.password,
            )
            conn.autocommit = False
            return conn

    def acquire(self):
        with self._lock:
            # SQLite: always reuse the same single connection
            # (separate connections don't share in-memory databases)
            if self.config.is_sqlite:
                if not self._connections:
                    conn = self._make_connection()
                    self._connections.append(conn)
                return self._connections[0]

            for conn in self._connections:
                if conn not in self._in_use:
                    try:
                        self._ping(conn)
                        self._in_use.add(conn)
                        return conn
                    except Exception:
                        self._connections.remove(conn)

            conn = self._make_connection()
            self._connections.append(conn)
            self._in_use.add(conn)
            return conn

    def release(self, conn):
        with self._lock:
            self._in_use.discard(conn)

    def _ping(self, conn):
        if self.config.is_sqlite:
            conn.execute("SELECT 1")
        else:
            conn.cursor().execute("SELECT 1")

    def close_all(self):
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            self._in_use.clear()


def connect(
    url: str = None,
    *,
    alias: str = "default",
    engine: str = "sqlite",
    name: str = ":memory:",
    host: str = "localhost",
    port: int = 5432,
    user: str = "",
    password: str = "",
    max_connections: int = 10,
) -> "_ConnectionPool":
    """
    Register a database connection.

    Usage (URL style):
        pookie.connect("postgresql://user:pass@localhost:5432/mydb")
        pookie.connect("sqlite:///mydb.sqlite3")

    Usage (kwargs style):
        pookie.connect(engine="sqlite", name="mydb.sqlite3")
        pookie.connect(engine="postgresql", name="mydb", host="localhost", user="postgres")
    """
    global _connections

    if url:
        config = DatabaseConfig(url=url)
    else:
        config = DatabaseConfig(engine=engine, name=name, host=host, port=port, user=user, password=password)

    pool = _ConnectionPool(config, max_connections=max_connections)
    _connections[alias] = pool
    return pool


def get_connection(alias: str = "default") -> "_ConnectionPool":
    """Retrieve a registered connection pool by alias."""
    if alias not in _connections:
        raise PookieConnectionError(
            f"No database registered with alias '{alias}'. "
            f"Call pookie.connect() before using models."
        )
    return _connections[alias]


@contextmanager
def transaction(alias: str = "default"):
    """Context manager for atomic transactions."""
    pool = get_connection(alias)
    conn = pool.acquire()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.release(conn)


def get_cursor(alias: str = "default"):
    """Return a (connection, cursor) pair from the pool."""
    pool = get_connection(alias)
    conn = pool.acquire()
    if pool.config.is_postgres:
        import psycopg2.extras
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cursor = conn.cursor()
    return pool, conn, cursor


def execute(sql: str, params=None, alias: str = "default", fetch: str = None, ignore_errors: bool = False):
    """
    Execute raw SQL. fetch can be 'one', 'all', or None.
    Returns rows if fetch is specified, else None.
    """
    pool, conn, cursor = get_cursor(alias)
    try:
        cursor.execute(sql, params or [])
        conn.commit()
        if fetch == "one":
            row = cursor.fetchone()
            return dict(row) if row else None
        elif fetch == "all":
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        return None
    except Exception:
        conn.rollback()
        if not ignore_errors:
            raise
        return None
    finally:
        cursor.close()
        pool.release(conn)
