import hashlib
import struct

from pookiedb.fields.core import Field, CharField, TextField, BooleanField
from pookiedb.exceptions import FieldError

HASH_FIELD = "_search_hash"
VECTOR_FIELD = "_search_embedding"
INDEXED_FIELD = "_search_indexed"   # SQLite + zvec: has this row's vector been pushed to zvec?
SEARCH_COLUMNS = (HASH_FIELD, VECTOR_FIELD, INDEXED_FIELD)

PG_VECTOR_MAX = 2000    # pgvector HNSW limit for `vector`
PG_HALFVEC_MAX = 4000   # pgvector HNSW limit for `halfvec`


class VectorField(Field):
    """
    Internal: stores a row's embedding. Added to searchable models by Pookie, never declared by users.
    - PostgreSQL: pgvector `vector(n)`, or `halfvec(n)` above 2,000 dimensions
    - SQLite: float32 BLOB
    """

    def __init__(self, dimensions: int, **kwargs):
        self.dimensions = dimensions
        kwargs.setdefault("null", True)
        kwargs.setdefault("editable", False)
        super().__init__(**kwargs)

    def pg_type(self) -> str:
        if self.dimensions <= PG_VECTOR_MAX:
            return "vector"
        if self.dimensions <= PG_HALFVEC_MAX:
            return "halfvec"
        raise FieldError(
            f"{self.dimensions} dimensions is above pgvector's indexable limit of {PG_HALFVEC_MAX}."
        )

    def sql_type(self, engine: str) -> str:
        if engine == "postgresql":
            return f"{self.pg_type()}({self.dimensions})"
        return "BLOB"

    def _engine(self) -> str:
        from pookiedb.db.connection import get_connection
        return get_connection(self.model._meta.db_alias).config.engine

    def to_db(self, value):
        if value is None:
            return None
        if self._engine() == "postgresql":
            return "[" + ",".join(repr(float(x)) for x in value) + "]"
        return struct.pack(f"<{len(value)}f", *value)

    def to_python(self, value):
        if value is None or isinstance(value, list):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            return list(struct.unpack(f"<{len(raw) // 4}f", raw))
        return [float(x) for x in str(value).strip("[]").split(",")]


def contribute_search_fields(cls):
    """Called by ModelBase: record searchable fields and add the hidden search columns."""
    from pookiedb.search.embedder import get_config

    opts = cls._meta
    opts.search_fields = [f for f in opts.fields if f.searchable]
    opts.search_vector = None

    for f in opts.search_fields:
        if not isinstance(f, (CharField, TextField)):
            raise FieldError(
                f"{cls.__name__}.{f.name}: searchable=True only works on CharField and TextField."
            )

    config = get_config()
    if not opts.search_fields or config is None:
        return  # keyword search only

    hidden = [
        (HASH_FIELD, CharField(max_length=64, null=True, editable=False)),
        (VECTOR_FIELD, VectorField(config.dimensions)),
        (INDEXED_FIELD, BooleanField(default=False, editable=False)),
    ]
    for name, field in hidden:
        field.contribute_to_class(cls, name)
        opts.add_field(field)
    opts.search_vector = opts.get_field(VECTOR_FIELD)


def search_text(instance, fields=None) -> str:
    fields = fields or instance._meta.search_fields
    parts = [instance.__dict__.get(f.name) for f in fields]
    return "\n\n".join(p for p in parts if p)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def prepare_for_write(model, objs: list) -> list:
    """
    Embed the objects whose searchable text changed, before anything is written.
    Returns the objects that got new search values. Raises EmbeddingError on failure.
    """
    from pookiedb.search.embedder import embed_texts

    if model._meta.search_vector is None:
        return []

    pending = []
    for obj in objs:
        text = search_text(obj)
        digest = text_hash(text)
        if obj.__dict__.get(HASH_FIELD) != digest:
            pending.append((obj, text, digest))

    to_embed = [text for _, text, _ in pending if text]
    vectors = iter(embed_texts(to_embed) if to_embed else [])
    for obj, text, digest in pending:
        obj.__dict__[VECTOR_FIELD] = next(vectors) if text else None
        obj.__dict__[HASH_FIELD] = digest
        obj.__dict__[INDEXED_FIELD] = False
    return [obj for obj, _, _ in pending]
