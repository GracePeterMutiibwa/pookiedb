"""
zvec as a search accelerator for SQLite.

The embeddings stored in the SQLite table are the source of truth; zvec only holds a copy
for fast nearest-neighbour search. Rows written since the last sync have
_search_indexed = false and are pushed to zvec just before a search.

zvec locks a collection exclusively while it's open for writing, so collections are
opened per operation and closed right after. Whenever zvec can't give a complete,
current answer (in-memory DB, locked by another process, any error), search() returns
None and the caller ranks the stored embeddings in Python instead.
"""

import logging
import os
import re
import shutil

from pookiedb.search.fields import VECTOR_FIELD, INDEXED_FIELD

log = logging.getLogger("pookiedb")

import zvec

VECTOR_NAME = "embedding"
OVERFETCH = 5  # filters are applied in SQLite afterwards, so ask zvec for extra candidates


def search(model, vector, n):
    """[(pk, similarity), ...] best first, or None when zvec can't be used."""
    path = _collection_path(model)
    if path is None:
        return None
    try:
        if not sync(model):
            return None
        collection = zvec.open(path=path, option=zvec.CollectionOption(read_only=True, enable_mmap=True))
        try:
            docs = collection.query(
                zvec.Query(field_name=VECTOR_NAME, vector=vector),
                topk=max(n * OVERFETCH, 50),
            )
        finally:
            del collection
    except Exception as e:
        log.warning("zvec search failed for %s, using exact search: %s", model.__name__, e)
        return None
    return [(doc.id, 1.0 - doc.score) for doc in docs]  # zvec returns cosine distance


def sync(model) -> bool:
    """Push rows with _search_indexed = false to zvec. False if that wasn't possible."""
    from pookiedb.db.connection import execute

    meta = model._meta
    table = meta.db_table
    alias = meta.db_alias
    path = _collection_path(model)
    pk_col = meta.pk.get_column_name()

    if not os.path.isdir(path):
        _create(model, path)

    rows = execute(
        f'SELECT "{pk_col}" AS pk, "{VECTOR_FIELD}" AS vec FROM "{table}" '
        f'WHERE "{INDEXED_FIELD}" = 0',
        alias=alias, fetch="all",
    ) or []
    if not rows:
        return True

    try:
        collection = zvec.open(path=path)
    except Exception as e:
        log.info("zvec collection for %s is busy, using exact search: %s", model.__name__, e)
        return False
    try:
        docs, clear = [], []
        for row in rows:
            if row["vec"] is None:
                clear.append(str(row["pk"]))   # text removed: drop any old vector
            else:
                vec = meta.search_vector.to_python(row["vec"])
                docs.append(zvec.Doc(id=str(row["pk"]), vectors={VECTOR_NAME: vec}))
        for i in range(0, len(docs), 500):
            _check(collection.upsert(docs[i : i + 500]))
        if clear:
            collection.delete(clear)  # "not found" is fine here
        collection.flush()
    finally:
        del collection

    ph = "?"
    ids = [row["pk"] for row in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        execute(
            f'UPDATE "{table}" SET "{INDEXED_FIELD}" = 1 '
            f'WHERE "{pk_col}" IN ({", ".join([ph] * len(chunk))})',
            chunk, alias=alias,
        )
    return True


def reset(model):
    """Delete the model's zvec data; the next search rebuilds it from SQLite."""
    from pookiedb.db.connection import execute

    path = _collection_path(model)
    if path is None:
        return
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    meta = model._meta
    execute(f'UPDATE "{meta.db_table}" SET "{INDEXED_FIELD}" = 0', alias=meta.db_alias)


def _create(model, path):
    """New collection: remove ones built for other dimensions and mark every row for sync."""
    from pookiedb.db.connection import execute

    parent = os.path.dirname(path)
    if os.path.isdir(parent):
        shutil.rmtree(parent)
    os.makedirs(parent)
    meta = model._meta
    schema = zvec.CollectionSchema(
        name=_collection_name(meta.db_table),
        vectors=zvec.VectorSchema(
            VECTOR_NAME,
            zvec.DataType.VECTOR_FP32,
            meta.search_vector.dimensions,
            index_param=zvec.HnswIndexParam(metric_type=zvec.MetricType.COSINE),
        ),
    )
    collection = zvec.create_and_open(path=path, schema=schema)
    del collection
    execute(f'UPDATE "{meta.db_table}" SET "{INDEXED_FIELD}" = 0', alias=meta.db_alias)


def _collection_path(model):
    """<db file>.zvec/<table>/d<dimensions>, or None when zvec can't be used."""
    from pookiedb.db.connection import get_connection

    meta = model._meta
    if meta.search_vector is None:
        return None
    cfg = get_connection(meta.db_alias).config
    if not cfg.is_sqlite or cfg.name == ":memory:":
        return None
    root = os.path.abspath(cfg.name) + ".zvec"
    return os.path.join(root, meta.db_table, f"d{meta.search_vector.dimensions}")


def _collection_name(table: str) -> str:
    return "pookie_" + re.sub(r"[^A-Za-z0-9_]", "_", table)


def _check(statuses):
    for status in statuses if isinstance(statuses, list) else [statuses]:
        if not status.ok():
            raise RuntimeError(f"zvec write failed: {status}")
