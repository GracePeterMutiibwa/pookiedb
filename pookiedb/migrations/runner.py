from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from pookiedb.exceptions import MigrationError


MIGRATIONS_TABLE = "pookie_migrations"


# ── Migration file template ───────────────────────────────────────────────────

MIGRATION_TEMPLATE = '''\
"""
Pookie ORM Auto-generated Migration
Generated: {timestamp}
Author: Grace Peter Mutiibwa
"""

from pookiedb.migrations.runner import Migration, CreateTable, AddColumn, DropColumn, AlterColumn, CreateIndex, DropTable, RawSQL


class Migration_{name}(Migration):
    name = "{name}"
    dependencies = {dependencies!r}

    operations = [
{operations}
    ]
'''


# ── Operation classes ─────────────────────────────────────────────────────────

class Operation:
    def forward_sql(self, engine: str) -> list[str]:
        raise NotImplementedError

    def backward_sql(self, engine: str) -> list[str]:
        return []

    def describe(self) -> str:
        return repr(self)


class CreateTable(Operation):
    def __init__(self, table: str, columns: list[dict], constraints: list[str] = None):
        self.table = table
        self.columns = columns  # list of {"name": ..., "sql": ...}
        self.constraints = constraints or []

    def forward_sql(self, engine: str) -> list[str]:
        parts = [f'    {c["sql"]}' for c in self.columns]
        parts += [f'    {c}' for c in self.constraints]
        body = ",\n".join(parts)
        return [f'CREATE TABLE IF NOT EXISTS "{self.table}" (\n{body}\n);']

    def backward_sql(self, engine: str) -> list[str]:
        return [f'DROP TABLE IF EXISTS "{self.table}";']

    def describe(self) -> str:
        return f"Create table '{self.table}'"

    def __repr__(self):
        cols_repr = repr(self.columns)
        constr_repr = repr(self.constraints)
        return f'CreateTable({self.table!r}, {cols_repr}, {constr_repr})'


class AddColumn(Operation):
    def __init__(self, table: str, column: str, sql: str, nullable: bool = True):
        self.table = table
        self.column = column
        self.sql = sql
        self.nullable = nullable

    def forward_sql(self, engine: str) -> list[str]:
        return [f'ALTER TABLE "{self.table}" ADD COLUMN {self.sql};']

    def backward_sql(self, engine: str) -> list[str]:
        if engine == "postgresql":
            return [f'ALTER TABLE "{self.table}" DROP COLUMN "{self.column}";']
        return []  # SQLite doesn't support DROP COLUMN easily

    def describe(self) -> str:
        return f"Add column '{self.column}' to '{self.table}'"

    def __repr__(self):
        return f'AddColumn({self.table!r}, {self.column!r}, {self.sql!r})'


class DropColumn(Operation):
    def __init__(self, table: str, column: str):
        self.table = table
        self.column = column

    def forward_sql(self, engine: str) -> list[str]:
        if engine == "postgresql":
            return [f'ALTER TABLE "{self.table}" DROP COLUMN "{self.column}";']
        return []

    def describe(self) -> str:
        return f"Drop column '{self.column}' from '{self.table}'"

    def __repr__(self):
        return f'DropColumn({self.table!r}, {self.column!r})'


class AlterColumn(Operation):
    def __init__(self, table: str, column: str, old_sql: str, new_sql: str):
        self.table = table
        self.column = column
        self.old_sql = old_sql
        self.new_sql = new_sql

    def forward_sql(self, engine: str) -> list[str]:
        if engine == "postgresql":
            # Parse type from new_sql
            new_type = self.new_sql.split()[0] if self.new_sql else "TEXT"
            return [
                f'ALTER TABLE "{self.table}" ALTER COLUMN "{self.column}" TYPE {new_type};'
            ]
        return []

    def describe(self) -> str:
        return f"Alter column '{self.column}' on '{self.table}'"

    def __repr__(self):
        return f'AlterColumn({self.table!r}, {self.column!r}, {self.old_sql!r}, {self.new_sql!r})'


class CreateIndex(Operation):
    def __init__(self, table: str, column: str, unique: bool = False):
        self.table = table
        self.column = column
        self.unique = unique

    def forward_sql(self, engine: str) -> list[str]:
        u = "UNIQUE " if self.unique else ""
        name = f"idx_{self.table}_{self.column}"
        return [f'CREATE {u}INDEX IF NOT EXISTS "{name}" ON "{self.table}" ("{self.column}");']

    def backward_sql(self, engine: str) -> list[str]:
        name = f"idx_{self.table}_{self.column}"
        return [f'DROP INDEX IF EXISTS "{name}";']

    def describe(self) -> str:
        return f"Create {'unique ' if self.unique else ''}index on '{self.table}'.'{self.column}'"

    def __repr__(self):
        return f'CreateIndex({self.table!r}, {self.column!r}, unique={self.unique!r})'


class DropTable(Operation):
    def __init__(self, table: str):
        self.table = table

    def forward_sql(self, engine: str) -> list[str]:
        return [f'DROP TABLE IF EXISTS "{self.table}";']

    def describe(self) -> str:
        return f"Drop table '{self.table}'"

    def __repr__(self):
        return f'DropTable({self.table!r})'


class RawSQL(Operation):
    def __init__(self, forward: str, backward: str = ""):
        self._forward = forward
        self._backward = backward

    def forward_sql(self, engine: str) -> list[str]:
        return [self._forward]

    def backward_sql(self, engine: str) -> list[str]:
        return [self._backward] if self._backward else []

    def describe(self) -> str:
        return f"Raw SQL: {self._forward[:60]}..."

    def __repr__(self):
        return f'RawSQL({self._forward!r})'


# ── Migration base class ──────────────────────────────────────────────────────

class Migration:
    name: str = ""
    dependencies: list[str] = []
    operations: list[Operation] = []

    def apply(self, engine: str, execute_fn):
        for op in self.operations:
            for sql in op.forward_sql(engine):
                execute_fn(sql)

    def unapply(self, engine: str, execute_fn):
        if engine == "sqlite":
            execute_fn("PRAGMA foreign_keys = OFF")
        for op in reversed(self.operations):
            for sql in op.backward_sql(engine):
                execute_fn(sql)
        if engine == "sqlite":
            execute_fn("PRAGMA foreign_keys = ON")


# ── Migration state snapshot (for auto-detect) ────────────────────────────────

def _snapshot_model(model) -> dict:
    """Capture a model's current schema as a dict for diff comparison."""
    from pookiedb.fields.related import ForeignKey, ManyToManyField
    fields = {}
    for field in model._meta.fields:
        if isinstance(field, ManyToManyField):
            continue
        col = field.get_column_name()
        entry = {
            "type": field.__class__.__name__,
            "null": field.null,
            "unique": field.unique,
            "primary_key": field.primary_key,
            "db_column": col,
            "field_name": field.name,
        }
        if isinstance(field, ForeignKey):
            entry["related_table"] = field.related_table
        fields[col] = entry
    return {
        "table": model._meta.db_table,
        "fields": fields,
    }


def _load_snapshot(migrations_dir: str) -> dict:
    """Load last known schema snapshot from .pookie_snapshot.json."""
    path = os.path.join(migrations_dir, ".pookie_snapshot.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_snapshot(migrations_dir: str, snapshot: dict):
    path = os.path.join(migrations_dir, ".pookie_snapshot.json")
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)


# ── makemigrations logic ──────────────────────────────────────────────────────

def make_migrations(
    migrations_dir: str,
    name: str = None,
    db_alias: str = "default",
) -> Optional[str]:
    """
    Compare current models against last snapshot, generate a new migration file.
    Returns the path to the new migration file, or None if no changes detected.
    """
    from pookiedb.db.registry import registry
    from pookiedb.db.connection import get_connection
    from pookiedb.fields.related import ForeignKey, ManyToManyField

    os.makedirs(migrations_dir, exist_ok=True)
    pool = get_connection(db_alias)
    engine = pool.config.engine

    # Load last snapshot
    old_snapshot = _load_snapshot(migrations_dir)
    operations = []
    new_snapshot = {}

    for model in registry.all():
        if model._meta.abstract:
            continue
        if model._meta.db_alias != db_alias:
            continue

        current = _snapshot_model(model)
        table = current["table"]
        new_snapshot[table] = current

        old = old_snapshot.get(table)

        if old is None:
            # New table
            columns = []
            constraints = []
            for field in model._meta.fields:
                if isinstance(field, ManyToManyField):
                    continue
                defn = field.sql_definition(engine)
                if defn:
                    columns.append({"name": field.get_column_name(), "sql": defn})
                if isinstance(field, ForeignKey):
                    constraints.append(field.sql_constraint(engine))
            for combo in (model._meta.unique_together or []):
                cols = ", ".join(f'"{c}"' for c in combo)
                constraints.append(f'UNIQUE ({cols})')
            operations.append(CreateTable(table, columns, constraints))

            # M2M join tables
            for m2m in model._meta.m2m_fields:
                m2m_sql = m2m.get_join_table_sql(engine)
                operations.append(RawSQL(m2m_sql))

            # Indexes
            for field in model._meta.fields:
                if field.db_index and not field.primary_key and not field.unique:
                    operations.append(CreateIndex(table, field.get_column_name()))

        else:
            # Diff columns
            old_fields = old.get("fields", {})
            new_fields = current.get("fields", {})

            for col, info in new_fields.items():
                if col not in old_fields:
                    # Added column
                    for field in model._meta.fields:
                        if field.get_column_name() == col:
                            defn = field.sql_definition(engine)
                            if defn:
                                operations.append(AddColumn(table, col, defn, field.null))
                            break

            for col in old_fields:
                if col not in new_fields:
                    operations.append(DropColumn(table, col))

    if not operations:
        return None

    # Determine next migration number
    existing = sorted(
        f for f in os.listdir(migrations_dir)
        if f.endswith(".py") and not f.startswith("_")
    )
    next_num = len(existing) + 1
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    migration_name = name or f"{next_num:04d}_auto_{ts}"

    # Build dependencies list
    dependencies = []
    if existing:
        last = existing[-1].replace(".py", "")
        dep_name = re.sub(r"^class Migration_", "", last)
        dependencies = [dep_name]

    # Render operations as Python repr
    ops_lines = []
    for op in operations:
        ops_lines.append(f"        {repr(op)},")
    operations_str = "\n".join(ops_lines)

    content = MIGRATION_TEMPLATE.format(
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        name=migration_name,
        dependencies=dependencies,
        operations=operations_str,
    )

    filepath = os.path.join(migrations_dir, f"{migration_name}.py")
    with open(filepath, "w") as f:
        f.write(content)

    _save_snapshot(migrations_dir, new_snapshot)
    return filepath
