"""DDL for searchable models, shared by create_table() and migrations."""

from pookiedb.search.fields import VECTOR_FIELD, INDEXED_FIELD

TS_CONFIG = "english"


def text_sql(fields) -> str:
    """Searchable columns joined into one text expression (unqualified, so indexes match)."""
    return " || ' ' || ".join(f"coalesce(\"{f.get_column_name()}\", '')" for f in fields)


def tsvector_sql(fields) -> str:
    return f"to_tsvector('{TS_CONFIG}', {text_sql(fields)})"


# Enables pgvector, or fails with a message that says what to do. Plain SQL, so it works the
# same from create_table() and from generated migration files. (No '%' or apostrophes: the
# text goes through psycopg2 parameter formatting and a quoted PL/pgSQL string.)
ENABLE_PGVECTOR_SQL = """\
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        RAISE EXCEPTION 'Semantic search on PostgreSQL needs the pgvector extension, which is not installed on this database server.'
            USING HINT = 'Install pgvector on the server (e.g. apt install postgresql-<version>-pgvector, or the pgvector/pgvector Docker image; most hosted PostgreSQL includes it), or remove pookiedb.embeddings() to use keyword-only search.';
    END IF;
    CREATE EXTENSION vector;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE EXCEPTION 'pgvector is installed on the server, but this database user is not allowed to enable it.'
        USING HINT = 'Ask a database admin to run CREATE EXTENSION vector; once in this database.';
END
$$;"""


def setup_sql(model, engine: str) -> list[str]:
    """Statements that must run before the table is created."""
    if engine == "postgresql" and model._meta.search_vector is not None:
        return [ENABLE_PGVECTOR_SQL]
    return []


def index_sql(model, engine: str) -> list[tuple[str, str]]:
    """(create, drop) statements for the search indexes of a model."""
    meta = model._meta
    table = meta.db_table
    statements = []

    if engine == "postgresql":
        if meta.search_fields:
            name = f"{table}_search_fts"
            statements.append((
                f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" '
                f'USING gin ({tsvector_sql(meta.search_fields)});',
                f'DROP INDEX IF EXISTS "{name}";',
            ))
        if meta.search_vector is not None:
            name = f"{table}_search_vec"
            ops = f"{meta.search_vector.pg_type()}_cosine_ops"
            statements.append((
                f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" '
                f'USING hnsw ("{VECTOR_FIELD}" {ops});',
                f'DROP INDEX IF EXISTS "{name}";',
            ))
    elif meta.search_vector is not None:
        name = f"{table}_search_pending"
        statements.append((
            f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ("{INDEXED_FIELD}");',
            f'DROP INDEX IF EXISTS "{name}";',
        ))
    return statements
