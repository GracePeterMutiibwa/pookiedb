from __future__ import annotations
import importlib.util
import os
import sys

from pookiedb.exceptions import MigrationError
from pookiedb.migrations.runner import Migration, MIGRATIONS_TABLE


def _ensure_migrations_table(execute_fn, engine: str):
    """Create the pookie_migrations tracking table if it doesn't exist."""
    if engine == "sqlite":
        pk_sql = '"id" INTEGER PRIMARY KEY AUTOINCREMENT'
    else:
        pk_sql = '"id" SERIAL PRIMARY KEY'
    sql = (
        f'CREATE TABLE IF NOT EXISTS "{MIGRATIONS_TABLE}" (\n'
        f'    {pk_sql},\n'
        f'    "name" VARCHAR(255) NOT NULL UNIQUE,\n'
        f'    "applied_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n'
        f');'
    )
    execute_fn(sql)


def _get_applied(fetch_fn) -> set[str]:
    rows = fetch_fn(f'SELECT "name" FROM "{MIGRATIONS_TABLE}"', []) or []
    return {r["name"] for r in rows}


def _load_migration_class(filepath: str) -> type:
    """Dynamically import a migration file and return its Migration subclass."""
    module_name = os.path.splitext(os.path.basename(filepath))[0]
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    for attr in dir(module):
        obj = getattr(module, attr)
        if (
            isinstance(obj, type)
            and issubclass(obj, Migration)
            and obj is not Migration
        ):
            return obj
    raise MigrationError(f"No Migration subclass found in {filepath}")


def migrate(
    migrations_dir: str,
    db_alias: str = "default",
    fake: bool = False,
) -> list[str]:
    """
    Apply all pending migrations in migrations_dir.

    Returns a list of migration names that were applied.
    """
    from pookiedb.db.connection import get_connection, execute

    pool = get_connection(db_alias)
    engine = pool.config.engine

    def execute_fn(sql, params=None):
        execute(sql, params or [], alias=db_alias)

    def fetch_fn(sql, params=None):
        return execute(sql, params or [], alias=db_alias, fetch="all")

    _ensure_migrations_table(execute_fn, engine)
    applied = _get_applied(fetch_fn)

    # Collect all migration files in order
    if not os.path.isdir(migrations_dir):
        return []

    files = sorted(
        f for f in os.listdir(migrations_dir)
        if f.endswith(".py") and not f.startswith("_")
    )

    applied_now = []
    for fname in files:
        mig_name = fname.replace(".py", "")
        if mig_name in applied:
            continue

        filepath = os.path.join(migrations_dir, fname)
        mig_cls = _load_migration_class(filepath)
        instance = mig_cls()

        if not fake:
            instance.apply(engine, execute_fn)

        # Record as applied
        ph = "?" if engine == "sqlite" else "%s"
        execute_fn(
            f'INSERT INTO "{MIGRATIONS_TABLE}" ("name") VALUES ({ph})',
            [mig_name],
        )
        applied_now.append(mig_name)

    return applied_now


def rollback(
    migrations_dir: str,
    db_alias: str = "default",
    steps: int = 1,
) -> list[str]:
    """
    Roll back the last `steps` applied migrations.
    Returns list of migration names that were rolled back.
    """
    from pookiedb.db.connection import get_connection, execute

    pool = get_connection(db_alias)
    engine = pool.config.engine

    def execute_fn(sql, params=None):
        execute(sql, params or [], alias=db_alias)

    def fetch_fn(sql, params=None):
        return execute(sql, params or [], alias=db_alias, fetch="all")

    _ensure_migrations_table(execute_fn, engine)
    applied_rows = fetch_fn(
        f'SELECT "name" FROM "{MIGRATIONS_TABLE}" ORDER BY "applied_at" DESC LIMIT {steps}'
    ) or []

    rolled_back = []
    for row in applied_rows:
        mig_name = row["name"]
        filepath = os.path.join(migrations_dir, f"{mig_name}.py")
        if not os.path.exists(filepath):
            continue
        mig_cls = _load_migration_class(filepath)
        instance = mig_cls()
        instance.unapply(engine, execute_fn)
        ph = "?" if engine == "sqlite" else "%s"
        execute_fn(
            f'DELETE FROM "{MIGRATIONS_TABLE}" WHERE "name" = {ph}',
            [mig_name],
        )
        rolled_back.append(mig_name)

    return rolled_back


def show_migrations(
    migrations_dir: str,
    db_alias: str = "default",
) -> list[dict]:
    """Return list of all migrations with their applied status."""
    from pookiedb.db.connection import get_connection, execute

    pool = get_connection(db_alias)
    engine = pool.config.engine

    def execute_fn(sql, params=None):
        execute(sql, params or [], alias=db_alias)

    def fetch_fn(sql, params=None):
        return execute(sql, params or [], alias=db_alias, fetch="all")

    _ensure_migrations_table(execute_fn, engine)
    applied = _get_applied(fetch_fn)

    if not os.path.isdir(migrations_dir):
        return []

    files = sorted(
        f for f in os.listdir(migrations_dir)
        if f.endswith(".py") and not f.startswith("_")
    )

    result = []
    for fname in files:
        mig_name = fname.replace(".py", "")
        result.append({"name": mig_name, "applied": mig_name in applied})
    return result
