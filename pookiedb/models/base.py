from __future__ import annotations
from typing import Any, Optional

from pookiedb.models.metaclass import ModelBase, Options
from pookiedb.exceptions import ValidationError, FieldError
from pookiedb.fields.core import AutoField
from pookiedb.fields.related import ManyToManyField, ForeignKey


class ModelState:
    """Tracks instance state (which DB it belongs to, whether it's been saved)."""
    def __init__(self):
        self.adding = True   # True if this instance hasn't been saved yet
        self.db = "default"


class Model(metaclass=ModelBase):
    """
    Base class for all Pookie models.

    Usage:
        class Author(pookie.Model):
            name = pookie.CharField(max_length=100)
            email = pookie.EmailField(unique=True)
            created_at = pookie.DateTimeField(auto_now_add=True)

            class Meta:
                db_table = "authors"
                ordering = ["-created_at"]
    """

    class Meta:
        abstract = True

    def __init__(self, **kwargs):
        self._state = ModelState()
        self._state.db = self._meta.db_alias

        pk_name = self._meta.pk.name
        for key in (pk_name, "pk"):
            if key in kwargs:
                raise FieldError(
                    f"{self.__class__.__name__}.{pk_name} is the primary key and is generated "
                    f"automatically; it can't be set."
                )

        # Set defaults for all fields
        for field in self._meta.fields:
            if field.name in kwargs:
                val = kwargs[field.name]
                # Support assigning FK column directly (e.g. author_id=1)
                if isinstance(field, ForeignKey):
                    if hasattr(val, '_meta'):
                        self.__dict__[field.db_column] = getattr(val, val._meta.pk.name)
                        self.__dict__[f'_cache_{field.name}'] = val
                    else:
                        self.__dict__[field.db_column] = val
                else:
                    self.__dict__[field.name] = field.to_python(val)
            else:
                # Check if FK column value was provided
                if isinstance(field, ForeignKey) and field.db_column in kwargs:
                    self.__dict__[field.db_column] = kwargs[field.db_column]
                else:
                    default = field.get_default()
                    if default is not None:
                        self.__dict__[field.name] = field.to_python(default)
                    else:
                        self.__dict__[field.name] = None

        # Handle extra kwargs (e.g. fk_id shorthand)
        for k, v in kwargs.items():
            if k not in {f.name for f in self._meta.fields}:
                if k not in {f.db_column for f in self._meta.fields}:
                    pass  # Silently ignore unknown kwargs (or raise?)

    def __repr__(self):
        pk = getattr(self, self._meta.pk.name, None)
        return f"<{self.__class__.__name__}: pk={pk}>"

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return getattr(self, self._meta.pk.name) == getattr(other, other._meta.pk.name)

    def __hash__(self):
        return hash((self.__class__, getattr(self, self._meta.pk.name)))

    # ── Field access ─────────────────────────────────────────────────────────

    def __getattribute__(self, name):
        # Let normal attribute lookup proceed
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        # If the name is a known model field, convert via to_python
        if not name.startswith("_"):
            try:
                meta = object.__getattribute__(self, "_meta")
                if name in (meta.pk.name, "pk"):
                    raise FieldError(
                        f"{self.__class__.__name__}.{meta.pk.name} is the primary key and is "
                        f"generated automatically; it can't be set."
                    )
                for field in meta.fields:
                    if field.name == name and not isinstance(field, (ForeignKey, ManyToManyField)):
                        self.__dict__[name] = field.to_python(value)
                        return
                    elif isinstance(field, ForeignKey) and field.name == name:
                        # Handled by descriptor
                        field.__set__(self, value)
                        return
            except AttributeError:
                pass
        super().__setattr__(name, value)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, update_fields: list = None):
        """Insert or update this model instance in the database."""
        from pookiedb.db.connection import get_connection, execute

        meta = self._meta
        pool = get_connection(meta.db_alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        table = meta.db_table
        pk_field = meta.pk
        pk_name = pk_field.name
        pk_col = pk_field.get_column_name()
        pk_val = pk_field.to_db(self.__dict__.get(pk_name))
        # AutoField pks come from the DB; AutoUUIDField pks are generated in __init__
        db_generated_pk = isinstance(pk_field, AutoField)

        # Run pre_save hooks (auto_now, auto_now_add)
        is_adding = self._state.adding
        for field in meta.fields:
            if hasattr(field, "pre_save"):
                field.pre_save(self, add=is_adding)

        # Run validation
        self.full_clean()

        if is_adding:
            # INSERT
            fields_to_insert = [
                f for f in meta.fields
                if not (f.primary_key and db_generated_pk)
                and not isinstance(f, ManyToManyField)
            ]
            if update_fields:
                fields_to_insert = [
                    f for f in fields_to_insert if f.name in update_fields or f.primary_key
                ]

            cols = ", ".join(f'"{f.get_column_name()}"' for f in fields_to_insert)
            placeholders = ", ".join([ph] * len(fields_to_insert))
            params = []
            for f in fields_to_insert:
                val = self.__dict__.get(f.db_column if isinstance(f, ForeignKey) else f.name)
                params.append(f.to_db(val))

            if pool.config.is_sqlite:
                sql = f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})'
                from pookiedb.db.connection import get_cursor as _get_cursor
                _pool, _conn, _cur = _get_cursor(meta.db_alias)
                try:
                    _cur.execute(sql, params)
                    _conn.commit()
                    new_pk = _cur.lastrowid
                except Exception:
                    _conn.rollback()
                    raise
                finally:
                    _cur.close()
                    _pool.release(_conn)
            else:
                sql = f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders}) RETURNING "{pk_col}"'
                row = execute(sql, params, alias=meta.db_alias, fetch="one")
                new_pk = row[pk_col] if row else None

            if db_generated_pk:
                self.__dict__[pk_name] = new_pk
            self._state.adding = False

        else:
            # UPDATE
            fields_to_update = [
                f for f in meta.fields
                if not f.primary_key
                and not isinstance(f, ManyToManyField)
                and (f.editable or hasattr(f, "auto_now"))
            ]
            if update_fields:
                fields_to_update = [f for f in fields_to_update if f.name in update_fields]

            if not fields_to_update:
                return

            set_parts = [f'"{f.get_column_name()}" = {ph}' for f in fields_to_update]
            params = []
            for f in fields_to_update:
                val = self.__dict__.get(f.db_column if isinstance(f, ForeignKey) else f.name)
                params.append(f.to_db(val))
            params.append(pk_val)

            sql = f'UPDATE "{table}" SET {", ".join(set_parts)} WHERE "{pk_col}" = {ph}'
            execute(sql, params, alias=meta.db_alias)

    def delete(self):
        """Delete this instance from the database."""
        from pookiedb.db.connection import execute, get_connection
        meta = self._meta
        pool = get_connection(meta.db_alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        pk_col = meta.pk.get_column_name()
        pk_val = meta.pk.to_db(self.__dict__.get(meta.pk.name))
        sql = f'DELETE FROM "{meta.db_table}" WHERE "{pk_col}" = {ph}'
        execute(sql, [pk_val], alias=meta.db_alias)
        self._state.adding = True

    def refresh_from_db(self):
        """Re-fetch the latest values from the database."""
        pk_val = self.__dict__.get(self._meta.pk.name)
        fresh = self.__class__.objects.get(pk=pk_val)
        self.__dict__.update(fresh.__dict__)

    # ── Validation ────────────────────────────────────────────────────────────

    def full_clean(self):
        """Run all field validators."""
        from pookiedb.fields.related import ForeignKey, ManyToManyField
        errors = {}
        for field in self._meta.fields:
            if isinstance(field, ManyToManyField):
                continue
            # Skip auto fields (auto_now, auto_now_add) — they set themselves on save
            if hasattr(field, 'auto_now') and (field.auto_now or field.auto_now_add):
                continue
            # FK values are stored under db_column (e.g. "author_id"), not field.name
            if isinstance(field, ForeignKey):
                val = self.__dict__.get(field.db_column)
            else:
                val = self.__dict__.get(field.name)
            try:
                field.validate(val, self)
            except ValidationError as e:
                errors[field.name] = str(e)
        if errors:
            raise ValidationError(f"Validation failed: {errors}")

    def clean(self):
        """Override this for model-level validation."""
        pass

    # ── Schema helpers ────────────────────────────────────────────────────────

    @classmethod
    def _schema_sql(cls, engine: str) -> str:
        """Generate CREATE TABLE SQL for this model."""
        meta = cls._meta
        table = meta.db_table
        col_defs = []
        constraints = []

        for field in meta.fields:
            if isinstance(field, ManyToManyField):
                continue
            defn = field.sql_definition(engine)
            if defn:
                col_defs.append(f"    {defn}")
            from pookiedb.fields.related import ForeignKey
            if isinstance(field, ForeignKey):
                constraints.append(f"    {field.sql_constraint(engine)}")

        # unique_together constraints — resolve field names to actual DB column names
        for combo in (meta.unique_together or []):
            resolved = []
            for c in combo:
                try:
                    f = meta.get_field(c)
                    resolved.append(f'"{f.get_column_name()}"')
                except KeyError:
                    resolved.append(f'"{c}"')
            constraints.append(f'    UNIQUE ({', '.join(resolved)})')

        all_parts = col_defs + constraints
        body = ",\n".join(all_parts)
        return f'CREATE TABLE IF NOT EXISTS "{table}" (\n{body}\n);'

    @classmethod
    def _from_row(cls, row: dict) -> "Model":
        """Build a model instance from a database row dict."""
        instance = cls.__new__(cls)
        instance._state = ModelState()
        instance._state.adding = False
        instance._state.db = cls._meta.db_alias
        instance.__dict__.update({})

        for field in cls._meta.fields:
            col = field.get_column_name()
            if isinstance(field, ForeignKey):
                raw = row.get(col) or row.get(field.name)
                instance.__dict__[field.db_column] = raw
            elif col in row:
                instance.__dict__[field.name] = field.to_python(row[col])
            elif field.name in row:
                instance.__dict__[field.name] = field.to_python(row[field.name])
            else:
                instance.__dict__[field.name] = None

        # Set pk alias
        pk = cls._meta.pk
        if pk:
            pk_val = instance.__dict__.get(pk.name)
            if pk_val is None:
                pk_val = row.get(pk.get_column_name())
            instance.__dict__[pk.name] = pk_val

        return instance

    @classmethod
    def create_table(cls, engine: str = None):
        """Create this model's table in the database."""
        from pookiedb.db.connection import execute, get_connection
        pool = get_connection(cls._meta.db_alias)
        eng = engine or pool.config.engine
        sql = cls._schema_sql(eng)
        execute(sql, alias=cls._meta.db_alias)

        # Create M2M join tables
        for m2m in cls._meta.m2m_fields:
            m2m_sql = m2m.get_join_table_sql(eng)
            execute(m2m_sql, alias=cls._meta.db_alias)

        # Create indexes
        for field in cls._meta.fields:
            if field.db_index and not field.primary_key and not field.unique:
                idx_sql = (
                    f'CREATE INDEX IF NOT EXISTS "idx_{cls._meta.db_table}_{field.get_column_name()}" '
                    f'ON "{cls._meta.db_table}" ("{field.get_column_name()}");'
                )
                execute(idx_sql, alias=cls._meta.db_alias)
