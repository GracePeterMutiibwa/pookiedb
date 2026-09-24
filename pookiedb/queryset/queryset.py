from __future__ import annotations
import copy
from typing import Any, Iterator, Optional, TYPE_CHECKING

from pookiedb.exceptions import DoesNotExist, MultipleObjectsReturned, FieldError
from pookiedb.fields.related import ForeignKey

if TYPE_CHECKING:
    pass


LOOKUP_MAP = {
    "exact": "= {ph}",
    "iexact": "ILIKE {ph}",
    "contains": "LIKE {ph}",
    "icontains": "ILIKE {ph}",
    "startswith": "LIKE {ph}",
    "istartswith": "ILIKE {ph}",
    "endswith": "LIKE {ph}",
    "iendswith": "ILIKE {ph}",
    "gt": "> {ph}",
    "gte": ">= {ph}",
    "lt": "< {ph}",
    "lte": "<= {ph}",
    "in": "IN ({ph})",
    "isnull": "IS NULL",
    "range": "BETWEEN {ph} AND {ph2}",
    "ne": "!= {ph}",
}

WILDCARD_LOOKUPS = {"contains", "icontains", "startswith", "istartswith", "endswith", "iendswith"}

SEARCH_MODES = ("keyword", "semantic", "hybrid")


def _wrap_wildcard(lookup: str, value: Any) -> Any:
    if lookup in ("contains", "icontains"):
        return f"%{value}%"
    if lookup in ("startswith", "istartswith"):
        return f"{value}%"
    if lookup in ("endswith", "iendswith"):
        return f"%{value}"
    return value


class Q:
    """
    Encapsulates query conditions, supporting & and | operators.

    Usage:
        Q(name="Alice") | Q(name="Bob")
        Q(age__gte=18) & Q(active=True)
    """

    AND = "AND"
    OR = "OR"

    def __init__(self, **kwargs):
        self.children = list(kwargs.items())
        self.connector = self.AND
        self.negated = False

    def __and__(self, other: "Q") -> "Q":
        q = Q()
        q.children = [self, other]
        q.connector = self.AND
        return q

    def __or__(self, other: "Q") -> "Q":
        q = Q()
        q.children = [self, other]
        q.connector = self.OR
        return q

    def __invert__(self) -> "Q":
        q = copy.deepcopy(self)
        q.negated = not q.negated
        return q

    def __repr__(self):
        return f"<Q: {self.children} connector={self.connector} negated={self.negated}>"


class QuerySet:
    """
    A lazy, chainable query interface for a Pookie model.

    Methods return a new QuerySet (immutable chain); queries are
    only executed when results are actually needed (iteration, len, etc.).
    """

    def __init__(self, model, db_alias: str = "default"):
        self.model = model
        self.db_alias = db_alias
        self._filters: list = []       # list of (Q | dict)
        self._excludes: list = []
        self._order_by: list = []
        self._limit: Optional[int] = None
        self._offset: int = 0
        self._select_related: list = []
        self._result_cache: Optional[list] = None
        self._evaluated = False
        self._fields: Optional[list] = None  # for values() / values_list()
        self._values_mode: Optional[str] = None  # "dict" | "tuple" | "flat"
        self._search: Optional[dict] = None  # set by search()

    def _clone(self) -> "QuerySet":
        qs = QuerySet(self.model, self.db_alias)
        qs._filters = copy.copy(self._filters)
        qs._excludes = copy.copy(self._excludes)
        qs._order_by = copy.copy(self._order_by)
        qs._limit = self._limit
        qs._offset = self._offset
        qs._select_related = copy.copy(self._select_related)
        qs._fields = copy.copy(self._fields)
        qs._values_mode = self._values_mode
        qs._search = self._search
        return qs

    # ── Filtering ────────────────────────────────────────────────────────────

    def filter(self, *q_objs, **kwargs) -> "QuerySet":
        qs = self._clone()
        for q in q_objs:
            qs._filters.append(q)
        if kwargs:
            qs._filters.append(Q(**kwargs))
        return qs

    def exclude(self, *q_objs, **kwargs) -> "QuerySet":
        qs = self._clone()
        for q in q_objs:
            qs._excludes.append(q)
        if kwargs:
            qs._excludes.append(Q(**kwargs))
        return qs

    def all(self) -> "QuerySet":
        return self._clone()

    # ── Ordering ─────────────────────────────────────────────────────────────

    def order_by(self, *fields) -> "QuerySet":
        self._reject_search("order_by()")
        qs = self._clone()
        qs._order_by = list(fields)
        return qs

    # ── Slicing ──────────────────────────────────────────────────────────────

    def limit(self, n: int) -> "QuerySet":
        qs = self._clone()
        qs._limit = n
        return qs

    def offset(self, n: int) -> "QuerySet":
        qs = self._clone()
        qs._offset = n
        return qs

    def __getitem__(self, key):
        if isinstance(key, slice):
            qs = self._clone()
            start = key.start or 0
            stop = key.stop
            qs._offset = start
            if stop is not None:
                qs._limit = stop - start
            return qs
        # Integer index
        qs = self._clone()
        qs._offset = key
        qs._limit = 1
        results = list(qs._fetch())
        if not results:
            raise IndexError("QuerySet index out of range")
        return results[0]

    # ── Projection ───────────────────────────────────────────────────────────

    def values(self, *fields) -> "QuerySet":
        self._reject_search("values()")
        qs = self._clone()
        qs._fields = list(fields) or None
        qs._values_mode = "dict"
        return qs

    def values_list(self, *fields, flat: bool = False) -> "QuerySet":
        self._reject_search("values_list()")
        qs = self._clone()
        qs._fields = list(fields) or None
        qs._values_mode = "flat" if flat else "tuple"
        return qs

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        mode: str = None,
        fields: list = None,
        min_similarity: float = None,
    ) -> "QuerySet":
        """
        Rank this queryset's rows against `query`. Results are model instances with
        `search_score` (0–1, higher is better) and `search_snippet` attached.

        mode: "keyword", "semantic" or "hybrid". Defaults to hybrid when embeddings
              are configured, otherwise keyword.
        fields: limit the search to some of the model's searchable fields.
        min_similarity: semantic cutoff; defaults to pookie.embeddings(min_similarity=...).

        Usage:
            Post.objects.filter(published=True).search("django migrations")[:5]
        """
        if mode is not None and mode not in SEARCH_MODES:
            raise ValueError(f"mode must be one of {SEARCH_MODES}, got {mode!r}.")
        if self._order_by:
            raise FieldError("search() orders results by relevance; remove order_by().")
        if self._values_mode:
            raise FieldError("search() returns model instances; remove values()/values_list().")
        if not self.model._meta.search_fields:
            raise FieldError(
                f"{self.model.__name__} has no searchable fields. "
                f"Add searchable=True to a CharField or TextField."
            )
        qs = self._clone()
        qs._search = {
            "query": query,
            "mode": mode,
            "fields": fields,
            "min_similarity": min_similarity,
        }
        return qs

    def _reject_search(self, what: str):
        if self._search is not None:
            raise FieldError(f"{what} can't be combined with search().")

    # ── Single object retrieval ──────────────────────────────────────────────

    def get(self, **kwargs) -> Any:
        if kwargs:
            qs = self.filter(**kwargs)
        else:
            qs = self
        results = list(qs._fetch())
        if len(results) == 0:
            raise DoesNotExist(
                f"{self.model.__name__} matching query does not exist."
            )
        if len(results) > 1:
            raise MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__}."
            )
        return results[0]

    def first(self) -> Optional[Any]:
        qs = self._clone()
        if not qs._order_by:
            pk_name = self.model._meta.pk.get_column_name()
            qs._order_by = [pk_name]
        qs._limit = 1
        results = list(qs._fetch())
        return results[0] if results else None

    def last(self) -> Optional[Any]:
        qs = self._clone()
        if not qs._order_by:
            pk_name = self.model._meta.pk.get_column_name()
            qs._order_by = [f"-{pk_name}"]
        else:
            qs._order_by = [f"-{f.lstrip('-')}" if not f.startswith('-') else f.lstrip('-') for f in qs._order_by]
        qs._limit = 1
        results = list(qs._fetch())
        return results[0] if results else None

    def get_or_create(self, defaults: dict = None, **kwargs):
        try:
            return self.get(**kwargs), False
        except DoesNotExist:
            params = dict(kwargs)
            if defaults:
                params.update(defaults)
            obj = self.model(**params)
            obj.save()
            return obj, True

    def update_or_create(self, defaults: dict = None, **kwargs):
        try:
            obj = self.get(**kwargs)
            for k, v in (defaults or {}).items():
                setattr(obj, k, v)
            obj.save()
            return obj, False
        except DoesNotExist:
            params = dict(kwargs)
            if defaults:
                params.update(defaults)
            obj = self.model(**params)
            obj.save()
            return obj, True

    # ── Aggregates ───────────────────────────────────────────────────────────

    def count(self) -> int:
        if self._search is not None:
            return len(self)
        sql, params = self._build_sql(select_override="COUNT(*) as cnt")
        row = self._execute(sql, params, fetch="one")
        return row["cnt"] if row else 0

    def exists(self) -> bool:
        return self.count() > 0

    def aggregate(self, **kwargs) -> dict:
        """
        Usage: qs.aggregate(total=Sum("amount"), avg_age=Avg("age"))
        """
        selects = []
        for alias, expr in kwargs.items():
            selects.append(f'{expr.sql()} AS "{alias}"')
        sql, params = self._build_sql(select_override=", ".join(selects))
        row = self._execute(sql, params, fetch="one")
        return dict(row) if row else {}

    def delete(self) -> int:
        self._reject_search("delete()")
        table = self.model._meta.db_table
        where_sql, params = self._build_where()
        sql = f'DELETE FROM "{table}"'
        if where_sql:
            sql += f" WHERE {where_sql}"
        from pookiedb.db.connection import execute
        execute(sql, params, alias=self.db_alias)
        return len(params)

    def bulk_update(self, **kwargs) -> int:
        self._reject_search("bulk_update()")
        table = self.model._meta.db_table
        pool = self._get_pool()
        ph = "?" if pool.config.is_sqlite else "%s"
        set_parts = []
        set_params = []
        for k, v in kwargs.items():
            field = self._resolve_field(k)
            if field.primary_key:
                raise FieldError(
                    f"{self.model.__name__}.{field.name} is the primary key and is generated "
                    f"automatically; it can't be set."
                )
            col = field.get_column_name()
            set_parts.append(f'"{col}" = {ph}')
            set_params.append(field.to_db(v))

        # Searchable text changed without re-embedding: clear the stale vectors.
        # `pookiedb embed` re-embeds them.
        meta = self.model._meta
        if meta.search_vector is not None and any(
            self._resolve_field(k) in meta.search_fields for k in kwargs
        ):
            from pookiedb.search.fields import HASH_FIELD, VECTOR_FIELD, INDEXED_FIELD
            set_parts += [f'"{HASH_FIELD}" = NULL', f'"{VECTOR_FIELD}" = NULL', f'"{INDEXED_FIELD}" = {ph}']
            set_params.append(False)

        where_sql, where_params = self._build_where()
        sql = f'UPDATE "{table}" SET {", ".join(set_parts)}'
        if where_sql:
            sql += f" WHERE {where_sql}"
        from pookiedb.db.connection import execute
        execute(sql, set_params + where_params, alias=self.db_alias)
        return 0

    # ── Iteration / evaluation ───────────────────────────────────────────────

    def __iter__(self) -> Iterator:
        if self._result_cache is None:
            self._result_cache = list(self._fetch())
        return iter(self._result_cache)

    def __len__(self) -> int:
        if self._result_cache is None:
            self._result_cache = list(self._fetch())
        return len(self._result_cache)

    def __bool__(self) -> bool:
        # Evaluate once and keep the rows, so `if qs:` followed by `for x in qs:` is one query
        if self._result_cache is None:
            self._result_cache = list(self._fetch())
        return bool(self._result_cache)

    def __repr__(self):
        results = list(self[:5])
        suffix = " ...more" if len(results) == 5 else ""
        return f"<QuerySet {results}{suffix}>"

    # ── Internal SQL building ─────────────────────────────────────────────────

    def _get_pool(self):
        from pookiedb.db.connection import get_connection
        return get_connection(self.db_alias)

    def _ph(self) -> str:
        return "?" if self._get_pool().config.is_sqlite else "%s"

    def _resolve_field(self, name: str):
        """Resolve a field name (possibly with __ lookup) to the field object."""
        parts = name.split("__")
        field_name = parts[0]
        # Allow 'pk' as an alias for the primary key field
        if field_name == "pk":
            return self.model._meta.pk
        fields = self.model._meta.fields
        for f in fields:
            if f.name == field_name or f.get_column_name() == field_name:
                return f
        raise FieldError(f"Unknown field '{field_name}' on model '{self.model.__name__}'.")

    def _parse_lookup(self, key: str, value: Any, ph: str) -> tuple[str, list]:
        """
        Convert a Django-style lookup (e.g. name__icontains) into SQL.
        Returns (sql_fragment, params).
        """
        parts = key.split("__")
        field_name = parts[0]
        lookup = parts[1] if len(parts) > 1 else "exact"
        table = self.model._meta.db_table

        # Resolve FK field to its column
        try:
            field = self._resolve_field(field_name)
            col = f'"{table}"."{field.get_column_name()}"'
        except FieldError:
            col = f'"{table}"."{field_name}"'
            field = None

        # Follow a relation: author__name="x" → author_id IN (SELECT id FROM authors WHERE name = x)
        if len(parts) > 1 and parts[1] not in LOOKUP_MAP:
            if not isinstance(field, ForeignKey):
                raise FieldError(f"Unknown lookup '{parts[1]}' on '{field_name}'.")
            related = field.related_model
            rel_table = related._meta.db_table
            rel_pk = related._meta.pk.get_column_name()
            sub_sql, sub_params = QuerySet(related, self.db_alias)._parse_lookup(
                "__".join(parts[1:]), value, ph
            )
            return (
                f'{col} IN (SELECT "{rel_table}"."{rel_pk}" FROM "{rel_table}" WHERE {sub_sql})',
                sub_params,
            )

        # If value is a model instance (e.g. filter(author=some_obj)), extract its pk
        if hasattr(value, '_meta'):
            value = getattr(value, value._meta.pk.name)

        # Convert Python values (e.g. uuid.UUID) the same way the field stores them
        if field is not None and lookup not in WILDCARD_LOOKUPS and lookup != "isnull":
            if lookup in ("in", "range"):
                value = [field.to_db(v) for v in value]
            else:
                value = field.to_db(value)

        if lookup == "isnull":
            if value:
                return f"{col} IS NULL", []
            else:
                return f"{col} IS NOT NULL", []

        if lookup == "in":
            if not value:
                return "1=0", []  # Empty IN → always false
            placeholders = ", ".join([ph] * len(value))
            return f"{col} IN ({placeholders})", list(value)

        if lookup == "range":
            return f"{col} BETWEEN {ph} AND {ph}", list(value)

        if lookup in WILDCARD_LOOKUPS:
            value = _wrap_wildcard(lookup, value)

        # SQLite doesn't support ILIKE, so use LIKE with LOWER()
        pool = self._get_pool()
        is_sqlite = pool.config.is_sqlite
        if is_sqlite and lookup in ("iexact", "icontains", "istartswith", "iendswith"):
            lower_col = f"LOWER({col})"
            lower_val = str(value).lower() if isinstance(value, str) else value
            return f"{lower_col} LIKE {ph}", [lower_val]

        op_template = LOOKUP_MAP.get(lookup, f"= {ph}")
        op = op_template.replace("{ph}", ph)
        return f"{col} {op}", [value]

    def _build_q(self, q: Q, ph: str) -> tuple[str, list]:
        """Recursively build SQL from a Q object."""
        if not q.children:
            return "1=1", []

        parts = []
        params = []

        for child in q.children:
            if isinstance(child, Q):
                sql, p = self._build_q(child, ph)
                parts.append(f"({sql})")
                params.extend(p)
            elif isinstance(child, tuple):
                key, value = child
                sql, p = self._parse_lookup(key, value, ph)
                parts.append(sql)
                params.extend(p)

        joined = f" {q.connector} ".join(parts)
        if q.negated:
            joined = f"NOT ({joined})"
        return joined, params

    def _build_where(self) -> tuple[str, list]:
        ph = self._ph()
        all_parts = []
        all_params = []

        for q in self._filters:
            sql, params = self._build_q(q, ph)
            all_parts.append(f"({sql})")
            all_params.extend(params)

        for q in self._excludes:
            sql, params = self._build_q(q, ph)
            all_parts.append(f"NOT ({sql})")
            all_params.extend(params)

        where_sql = " AND ".join(all_parts)
        return where_sql, all_params

    def _build_sql(self, select_override: str = None) -> tuple[str, list]:
        table = self.model._meta.db_table

        # SELECT
        if select_override:
            select_sql = select_override
        elif self._fields:
            resolved = []
            for f in self._fields:
                try:
                    field = self._resolve_field(f)
                    resolved.append(f'"{table}"."{field.get_column_name()}" AS "{f}"')
                except FieldError:
                    resolved.append(f'"{table}"."{f}"')
            select_sql = ", ".join(resolved)
        else:
            select_sql = self._default_select(include_hidden=not self._values_mode)

        sql = f'SELECT {select_sql} FROM "{table}"'

        # WHERE
        where_sql, params = self._build_where()
        if where_sql:
            sql += f" WHERE {where_sql}"

        # ORDER BY
        if self._order_by:
            order_parts = []
            for o in self._order_by:
                if o.startswith("-"):
                    order_parts.append(f'"{table}"."{o[1:]}" DESC')
                else:
                    order_parts.append(f'"{table}"."{o}" ASC')
            sql += f" ORDER BY {', '.join(order_parts)}"

        # LIMIT / OFFSET
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        if self._offset:
            sql += f" OFFSET {self._offset}"

        return sql, params

    def _default_select(self, include_hidden: bool = True) -> str:
        """All columns, minus the (large) embedding; minus all search columns for values()."""
        meta = self.model._meta
        table = meta.db_table
        if meta.search_vector is None:
            return f'"{table}".*'
        from pookiedb.search.fields import SEARCH_COLUMNS, VECTOR_FIELD
        skip = {VECTOR_FIELD} if include_hidden else set(SEARCH_COLUMNS)
        return ", ".join(
            f'"{table}"."{f.get_column_name()}"' for f in meta.fields if f.name not in skip
        )

    def _execute(self, sql: str, params: list, fetch: str = "all"):
        from pookiedb.db.connection import execute
        return execute(sql, params, alias=self.db_alias, fetch=fetch)

    def _fetch(self):
        if self._search is not None:
            from pookiedb.search.engine import run_search
            yield from run_search(self)
            return
        sql, params = self._build_sql()
        rows = self._execute(sql, params, fetch="all") or []
        for row in rows:
            if self._values_mode == "dict":
                if self._fields:
                    yield {f: row.get(f) for f in self._fields}
                else:
                    yield dict(row)
            elif self._values_mode == "tuple":
                if self._fields:
                    yield tuple(row.get(f) for f in self._fields)
                else:
                    yield tuple(row.values())
            elif self._values_mode == "flat":
                vals = list(row.values())
                yield vals[0] if vals else None
            else:
                yield self.model._from_row(row)


# ── Aggregate helpers ─────────────────────────────────────────────────────────

class _Aggregate:
    def __init__(self, field: str):
        self.field = field

    def sql(self) -> str:
        raise NotImplementedError


class Sum(_Aggregate):
    def sql(self): return f'SUM("{self.field}")'


class Avg(_Aggregate):
    def sql(self): return f'AVG("{self.field}")'


class Max(_Aggregate):
    def sql(self): return f'MAX("{self.field}")'


class Min(_Aggregate):
    def sql(self): return f'MIN("{self.field}")'


class Count(_Aggregate):
    def __init__(self, field="*", distinct=False):
        self.field = field
        self.distinct = distinct

    def sql(self):
        d = "DISTINCT " if self.distinct else ""
        return f'COUNT({d}"{self.field}")' if self.field != "*" else f'COUNT(*)'
