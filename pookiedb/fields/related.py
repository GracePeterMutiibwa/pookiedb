from pookiedb.fields.core import Field, IntegerField
from pookiedb.exceptions import FieldError


CASCADE = "CASCADE"
SET_NULL = "SET NULL"
SET_DEFAULT = "SET DEFAULT"
PROTECT = "RESTRICT"
DO_NOTHING = "NO ACTION"


class RelatedField(Field):
    """Base class for relationship fields."""

    def __init__(self, to, on_delete=CASCADE, related_name=None, **kwargs):
        self.to = to  # Model class or string "app.Model"
        self.on_delete = on_delete
        self.related_name = related_name
        super().__init__(**kwargs)

    def resolve_related_model(self):
        """Resolve a string model reference to the actual class."""
        if isinstance(self.to, str):
            from pookiedb.db.registry import registry
            model = registry.get(self.to)
            if model is None:
                raise FieldError(
                    f"Cannot resolve model '{self.to}' for field '{self.name}'. "
                    f"Make sure the model is imported and registered."
                )
            self.to = model
        return self.to

    @property
    def related_model(self):
        return self.resolve_related_model()

    @property
    def related_table(self) -> str:
        return self.related_model._meta.db_table


class ForeignKey(RelatedField):
    """
    Defines a many-to-one relationship.

    Usage:
        author = pookie.ForeignKey("User", on_delete=pookie.CASCADE)
    """

    def contribute_to_class(self, model, name: str):
        # Determine the DB column BEFORE calling super() which would set db_column=name
        # unless the user explicitly passed db_column= to the constructor.
        user_override = self.db_column  # None if not set by user
        super().contribute_to_class(model, name)
        # Always use <name>_id unless the user explicitly overrode db_column
        if not user_override:
            self.db_column = f"{name}_id"

    def sql_type(self, engine: str) -> str:
        # Match the referenced primary key (INTEGER, BIGINT, UUID/TEXT)
        return self.related_model._meta.pk.rel_db_type(engine)

    def sql_definition(self, engine: str) -> str:
        col = self.get_column_name()
        parts = [f'"{col}" {self.sql_type(engine)}']
        if not self.null:
            parts.append("NOT NULL")
        if self.unique:
            parts.append("UNIQUE")
        return " ".join(parts)

    def sql_constraint(self, engine: str) -> str:
        """Returns the FOREIGN KEY constraint SQL (added separately)."""
        col = self.get_column_name()
        related = self.related_model
        related_table = related._meta.db_table
        related_pk = related._meta.pk.get_column_name()
        return (
            f'FOREIGN KEY ("{col}") REFERENCES "{related_table}" ("{related_pk}") '
            f'ON DELETE {self.on_delete}'
        )

    def to_python(self, value):
        return value  # Raw FK id

    def to_db(self, value):
        return self.related_model._meta.pk.to_db(value)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        fk_val = instance.__dict__.get(self.db_column) or instance.__dict__.get(self.name)
        if fk_val is None:
            return None
        # Lazy load
        cache_key = f"_cache_{self.name}"
        if cache_key not in instance.__dict__:
            related = self.related_model
            instance.__dict__[cache_key] = related.objects.get(pk=fk_val)
        return instance.__dict__[cache_key]

    def __set__(self, instance, value):
        if value is None:
            instance.__dict__[self.db_column] = None
            instance.__dict__.pop(f"_cache_{self.name}", None)
        elif hasattr(value, "_meta"):
            # Assigned a model instance
            pk = getattr(value, value._meta.pk.name)
            instance.__dict__[self.db_column] = pk
            instance.__dict__[f"_cache_{self.name}"] = value
        else:
            # Assigned a raw pk (int or UUID)
            instance.__dict__[self.db_column] = value
            instance.__dict__.pop(f"_cache_{self.name}", None)


class OneToOneField(ForeignKey):
    """
    Defines a one-to-one relationship. Like ForeignKey but enforces uniqueness.

    Usage:
        profile = pookie.OneToOneField("UserProfile", on_delete=pookie.CASCADE)
    """

    def __init__(self, to, on_delete=CASCADE, **kwargs):
        kwargs["unique"] = True
        super().__init__(to, on_delete=on_delete, **kwargs)

    def sql_definition(self, engine: str) -> str:
        base = super().sql_definition(engine)
        if "UNIQUE" not in base:
            base += " UNIQUE"
        return base


class ManyToManyField(RelatedField):
    """
    Defines a many-to-many relationship via a join table.

    Usage:
        tags = pookie.ManyToManyField("Tag", related_name="posts")
    """

    def __init__(self, to, through=None, related_name=None, **kwargs):
        self.through = through  # Optional explicit join model
        kwargs.pop("null", None)
        kwargs.pop("default", None)
        super().__init__(to, on_delete=CASCADE, related_name=related_name, **kwargs)

    def contribute_to_class(self, model, name: str):
        self.name = name
        self.model = model
        if not self.db_column:
            self.db_column = name

    def get_join_table_name(self) -> str:
        """Auto-generate join table name: smaller_model_larger_model."""
        src = self.model._meta.db_table
        dst = self.related_table
        names = sorted([src, dst])
        return f"{names[0]}_{names[1]}"

    def get_join_table_sql(self, engine: str) -> str:
        """Generate CREATE TABLE SQL for the join table."""
        join_table = self.get_join_table_name()
        src_table = self.model._meta.db_table
        dst_table = self.related_table
        src_pk = self.model._meta.pk
        dst_pk = self.related_model._meta.pk
        src_col = f"{src_table}_id"
        dst_col = f"{dst_table}_id"
        return (
            f'CREATE TABLE IF NOT EXISTS "{join_table}" (\n'
            f'    "id" INTEGER PRIMARY KEY {"AUTOINCREMENT" if engine == "sqlite" else "GENERATED ALWAYS AS IDENTITY"},\n'
            f'    "{src_col}" {src_pk.rel_db_type(engine)} NOT NULL REFERENCES "{src_table}" ("{src_pk.get_column_name()}") ON DELETE CASCADE,\n'
            f'    "{dst_col}" {dst_pk.rel_db_type(engine)} NOT NULL REFERENCES "{dst_table}" ("{dst_pk.get_column_name()}") ON DELETE CASCADE,\n'
            f'    UNIQUE ("{src_col}", "{dst_col}")\n'
            f');'
        )

    def sql_definition(self, engine: str) -> str:
        return ""  # M2M fields don't add a column to the model table

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return ManyToManyManager(instance, self)

    def __set__(self, instance, value):
        raise AttributeError("ManyToManyField cannot be set directly. Use .add() or .set().")


class ManyToManyManager:
    """Manager returned when accessing a ManyToManyField on a model instance."""

    def __init__(self, instance, field: ManyToManyField):
        self.instance = instance
        self.field = field

    @property
    def _join_table(self):
        return self.field.get_join_table_name()

    @property
    def _src_col(self):
        return f"{self.instance._meta.db_table}_id"

    @property
    def _dst_col(self):
        return f"{self.field.related_table}_id"

    @property
    def _src_pk(self):
        return self._pk_to_db(self.instance)

    @staticmethod
    def _pk_to_db(obj):
        pk = obj._meta.pk
        return pk.to_db(getattr(obj, pk.name))

    def _db_alias(self):
        return self.instance._state.db or "default"

    def all(self):
        from pookiedb.db.connection import execute
        related = self.field.related_model
        join = self._join_table
        src = self._src_col
        dst = self._dst_col
        rel_table = related._meta.db_table
        rel_pk = related._meta.pk.get_column_name()
        sql = (
            f'SELECT r.* FROM "{rel_table}" r '
            f'INNER JOIN "{join}" j ON r."{rel_pk}" = j."{dst}" '
            f'WHERE j."{src}" = %s'
        )
        alias = self._db_alias()
        from pookiedb.db.connection import get_connection
        pool = get_connection(alias)
        if pool.config.is_sqlite:
            sql = sql.replace("%s", "?")
        rows = execute(sql, [self._src_pk], alias=alias, fetch="all")
        return [related._from_row(row) for row in (rows or [])]

    def add(self, *objs):
        from pookiedb.db.connection import execute, get_connection
        alias = self._db_alias()
        pool = get_connection(alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        for obj in objs:
            pk = self._pk_to_db(obj)
            sql = (
                f'INSERT INTO "{self._join_table}" ("{self._src_col}", "{self._dst_col}") '
                f'VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING'
            )
            if pool.config.is_sqlite:
                sql = (
                    f'INSERT OR IGNORE INTO "{self._join_table}" '
                    f'("{self._src_col}", "{self._dst_col}") VALUES (?, ?)'
                )
            execute(sql, [self._src_pk, pk], alias=alias)

    def remove(self, *objs):
        from pookiedb.db.connection import execute, get_connection
        alias = self._db_alias()
        pool = get_connection(alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        for obj in objs:
            pk = self._pk_to_db(obj)
            sql = (
                f'DELETE FROM "{self._join_table}" '
                f'WHERE "{self._src_col}" = {ph} AND "{self._dst_col}" = {ph}'
            )
            execute(sql, [self._src_pk, pk], alias=alias)

    def set(self, objs):
        self.clear()
        self.add(*objs)

    def clear(self):
        from pookiedb.db.connection import execute, get_connection
        alias = self._db_alias()
        pool = get_connection(alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        sql = f'DELETE FROM "{self._join_table}" WHERE "{self._src_col}" = {ph}'
        execute(sql, [self._src_pk], alias=alias)

    def count(self) -> int:
        from pookiedb.db.connection import execute, get_connection
        alias = self._db_alias()
        pool = get_connection(alias)
        ph = "?" if pool.config.is_sqlite else "%s"
        sql = f'SELECT COUNT(*) as c FROM "{self._join_table}" WHERE "{self._src_col}" = {ph}'
        row = execute(sql, [self._src_pk], alias=alias, fetch="one")
        return row["c"] if row else 0
