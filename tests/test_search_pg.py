"""
Search on PostgreSQL + pgvector. Skipped unless POOKIEDB_TEST_PG_URL is set, e.g.
    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=pookie pgvector/pgvector:pg17
    POOKIEDB_TEST_PG_URL=postgresql://postgres:pookie@localhost:55432/postgres pytest
"""

import os
import uuid

import pytest
import pookiedb
from pookiedb.search import embedder
from pookiedb.search.backfill import embed_missing
from pookiedb.search.fields import VECTOR_FIELD, HASH_FIELD
from tests.search_helpers import DIMS, FakeEmbedder, fake_vector

PG_URL = os.environ.get("POOKIEDB_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="POOKIEDB_TEST_PG_URL not set")

ALIAS = "search_pg"
TABLES = ("pg_pages", "pg_manuals", "pg_articles", "pg_migdocs", "pookie_migrations")

DOCS = [
    ("Refund policy", "We refund every order within 30 days. Contact support to request a refund."),
    ("Delivery times", "Orders are shipped within two days and the courier delivers in a week."),
    ("Account help", "Reset your password from the login page if you cannot sign in."),
]


def sql(query, params=None, fetch=None):
    return pookiedb.execute(query, params or [], alias=ALIAS, fetch=fetch)


@pytest.fixture(scope="module")
def m():
    pookiedb.connect(PG_URL, alias=ALIAS)
    for table in TABLES:
        sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')

    fake = FakeEmbedder()
    pookiedb.embeddings(fake, dimensions=DIMS, min_similarity=0.3)

    class PgManual(pookiedb.Model):
        title = pookiedb.CharField(max_length=100)
        class Meta:
            db_table = "pg_manuals"
            db_alias = ALIAS

    class PgPage(pookiedb.Model):
        manual  = pookiedb.ForeignKey(PgManual, on_delete=pookiedb.CASCADE)
        number  = pookiedb.IntegerField()
        heading = pookiedb.CharField(max_length=100, searchable=True)
        content = pookiedb.TextField(searchable=True)
        class Meta:
            db_table = "pg_pages"
            db_alias = ALIAS

    class PgArticle(pookiedb.Model):
        id   = pookiedb.AutoUUIDField()
        body = pookiedb.TextField(searchable=True)
        class Meta:
            db_table = "pg_articles"
            db_alias = ALIAS

    for model in (PgManual, PgPage, PgArticle):
        model.create_table()

    class NS: pass
    ns = NS()
    ns.Manual, ns.Page, ns.Article, ns.fake = PgManual, PgPage, PgArticle, fake
    yield ns
    embedder.reset()
    for table in TABLES:
        sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')


@pytest.fixture(autouse=True)
def clean(m):
    sql('DELETE FROM "pg_pages"')
    sql('DELETE FROM "pg_manuals"')
    sql('DELETE FROM "pg_articles"')
    m.fake.calls.clear()
    yield


def make_pages(m, title="Store handbook"):
    manual = m.Manual.objects.create(title=title)
    for i, (heading, content) in enumerate(DOCS):
        m.Page.objects.create(manual=manual, number=i + 1, heading=heading, content=content)
    return manual


def test_schema_extension_and_indexes(m):
    assert sql("SELECT 1 AS ok FROM pg_extension WHERE extname = 'vector'", fetch="one")
    col = sql(
        "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
        "WHERE attrelid = 'pg_pages'::regclass AND attname = %s", [VECTOR_FIELD], fetch="one")
    assert col["t"] == f"vector({DIMS})"
    indexes = {r["indexname"]: r["indexdef"] for r in sql(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'pg_pages'", fetch="all")}
    assert "USING hnsw" in indexes["pg_pages_search_vec"]
    assert "vector_cosine_ops" in indexes["pg_pages_search_vec"]
    assert "USING gin" in indexes["pg_pages_search_fts"]

def test_vector_stored_not_selected(m):
    make_pages(m)
    assert sql(f'SELECT COUNT(*) AS c FROM "pg_pages" WHERE "{VECTOR_FIELD}" IS NOT NULL', fetch="one")["c"] == 3
    page = m.Page.objects.first()
    assert page.__dict__[VECTOR_FIELD] is None
    page.number = 7
    page.save()
    assert sql(f'SELECT "{VECTOR_FIELD}" AS v FROM "pg_pages" WHERE "id" = %s', [page.id], fetch="one")["v"]

def test_keyword_full_text(m):
    make_pages(m)
    results = list(m.Page.objects.search("refunds", mode="keyword"))   # stemmed: refunds → refund
    assert results[0].heading == "Refund policy"
    assert 0 < results[0].search_score < 1
    assert "**refund**" in results[0].search_snippet.lower()

def test_keyword_falls_back_to_like(m):
    make_pages(m)
    results = list(m.Page.objects.search("passw", mode="keyword"))     # partial word: FTS misses
    assert [r.heading for r in results] == ["Account help"]
    assert "**passw**" in results[0].search_snippet.lower()

def test_semantic_pgvector(m):
    make_pages(m)
    results = list(m.Page.objects.search("money back", mode="semantic"))
    assert results[0].heading == "Refund policy"
    assert list(m.Page.objects.search("bananas", mode="semantic")) == []

def test_hybrid_with_relation_filter(m):
    make_pages(m, "Store handbook")
    staff = make_pages(m, "Staff guide")
    results = list(m.Page.objects.filter(manual__title="Staff guide").search("courier delivery")[:2])
    assert len(results) <= 2
    assert results[0].heading == "Delivery times" and results[0].manual_id == staff.id

def test_uuid_pk(m):
    for _, body in DOCS:
        m.Article.objects.create(body=body)
    results = list(m.Article.objects.search("money back"))
    assert isinstance(results[0].id, uuid.UUID) and "refund" in results[0].body

def test_bulk_update_marks_stale(m):
    make_pages(m)
    m.Page.objects.filter(number=1).bulk_update(content="Store credit only")
    assert sql(f'SELECT COUNT(*) AS c FROM "pg_pages" WHERE "{VECTOR_FIELD}" IS NULL', fetch="one")["c"] == 1
    assert embed_missing(m.Page) == 1

def test_migrations_resize_vector(m, tmp_path):
    from pookiedb.migrations.runner import make_migrations
    from pookiedb.migrations.executor import migrate, rollback
    from pookiedb.db.registry import registry

    saved = embedder.get_config()
    mig_dir = str(tmp_path / "migrations")

    def define(dims):
        pookiedb.embeddings(lambda t: [fake_vector(x, dims) for x in t], dimensions=dims)
        registry._models = {k: v for k, v in registry._models.items() if v.__name__ != "PgMigDoc"}
        return type("PgMigDoc", (pookiedb.Model,), {
            "__module__": __name__,
            "body": pookiedb.TextField(searchable=True),
            "Meta": type("Meta", (), {"db_table": "pg_migdocs", "db_alias": ALIAS}),
        })

    try:
        # Only PgMigDoc should be picked up by makemigrations on this alias
        others = {k: v for k, v in registry._models.items() if v._meta.db_alias == ALIAS}
        for k in others:
            registry._models.pop(k)

        Doc = define(DIMS)
        make_migrations(mig_dir, db_alias=ALIAS)
        migrate(mig_dir, db_alias=ALIAS)
        Doc.objects.create(body="refund")

        Doc = define(32)
        second = open(make_migrations(mig_dir, db_alias=ALIAS)).read()
        assert "DropColumn" in second and f'"{HASH_FIELD}" = NULL' in second
        migrate(mig_dir, db_alias=ALIAS)
        col = sql("SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                  "WHERE attrelid = 'pg_migdocs'::regclass AND attname = %s", [VECTOR_FIELD], fetch="one")
        assert col["t"] == "vector(32)"
        assert embed_missing(Doc) == 1
        assert Doc.objects.search("refund", mode="semantic")[0].body == "refund"
    finally:
        registry._models.update(others)
        embedder._config = saved
