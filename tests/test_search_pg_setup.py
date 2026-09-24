"""
The pgvector setup error. Needs a PostgreSQL server *without* pgvector, e.g.
    docker run -d -p 55433:5432 -e POSTGRES_PASSWORD=pookie postgres:17
    POOKIEDB_TEST_PG_NOVECTOR_URL=postgresql://postgres:pookie@localhost:55433/postgres pytest
"""

import os

import pytest
import pookiedb
from pookiedb.search import embedder

NOVECTOR_URL = os.environ.get("POOKIEDB_TEST_PG_NOVECTOR_URL")


@pytest.mark.skipif(not NOVECTOR_URL, reason="POOKIEDB_TEST_PG_NOVECTOR_URL not set")
def test_missing_pgvector_explains_what_to_do():
    saved = embedder.get_config()
    try:
        pookiedb.connect(NOVECTOR_URL, alias="novector")
        pookiedb.embeddings(lambda texts: [[1.0, 0.0] for _ in texts], dimensions=2)
        NoVecDoc = type("NoVecDoc", (pookiedb.Model,), {
            "__module__": __name__,
            "body": pookiedb.TextField(searchable=True),
            "Meta": type("Meta", (), {"db_table": "novec_docs", "db_alias": "novector"}),
        })
        with pytest.raises(Exception, match="needs the pgvector extension") as info:
            NoVecDoc.create_table()
        assert "remove pookiedb.embeddings()" in str(info.value)
    finally:
        embedder._config = saved
