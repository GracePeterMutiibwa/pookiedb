"""Bring stored embeddings up to date (used by `pookiedb embed`)."""

from pookiedb.search.fields import SEARCH_COLUMNS, prepare_for_write


def searchable_models():
    from pookiedb.db.registry import registry
    return [m for m in registry.all() if m._meta.search_vector is not None]


def embed_missing(model, batch_size: int = 200) -> int:
    """
    Embed rows whose searchable text has no current embedding (rows written with
    bulk_update() or raw SQL, rows from before the field was searchable, or after a
    dimensions change). Returns how many rows were embedded.
    """
    from pookiedb.db.connection import execute, get_connection

    meta = model._meta
    table = meta.db_table
    pk = meta.pk
    ph = "?" if get_connection(meta.db_alias).config.is_sqlite else "%s"
    hidden = [meta.get_field(name) for name in SEARCH_COLUMNS]
    set_sql = ", ".join(f'"{f.get_column_name()}" = {ph}' for f in hidden)

    embedded = 0
    offset = 0
    while True:
        batch = list(model.objects.order_by(pk.name).offset(offset).limit(batch_size))
        if not batch:
            return embedded
        offset += len(batch)
        for obj in prepare_for_write(model, batch):
            params = [f.to_db(obj.__dict__.get(f.name)) for f in hidden]
            params.append(pk.to_db(getattr(obj, pk.name)))
            execute(
                f'UPDATE "{table}" SET {set_sql} WHERE "{pk.get_column_name()}" = {ph}',
                params, alias=meta.db_alias,
            )
            embedded += 1


def reindex(model):
    """Rebuild the zvec copy of a model's embeddings (SQLite only; no-op otherwise)."""
    from pookiedb.search import zvec_store
    zvec_store.reset(model)
