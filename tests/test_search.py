"""
Search on SQLite: embedding contract, searchable models, keyword / semantic / hybrid
search, relation filters, zvec acceleration, migrations.
PostgreSQL coverage lives in test_search_pg.py.
"""

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import pookiedb
from pookiedb.search import embedder, zvec_store
from pookiedb.search.backfill import embed_missing
from pookiedb.search.fields import HASH_FIELD, VECTOR_FIELD, INDEXED_FIELD
from tests.search_helpers import DIMS, FakeEmbedder, fake_vector

ALIAS = "search_mem"

DOCS = [
    ("Refund policy", "We refund every order within 30 days. Contact support to request a refund."),
    ("Delivery times", "Orders are shipped within two days and the courier delivers in a week."),
    ("Account help", "Reset your password from the login page if you cannot sign in."),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Models (module-scoped, in-memory SQLite → exact Python ranking)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture(scope="module")
def m():
    pookiedb.connect(engine="sqlite", name=":memory:", alias=ALIAS)

    # Defined before embeddings() → keyword search only
    class Note(pookiedb.Model):
        body = pookiedb.TextField(searchable=True)
        class Meta:
            db_table = "notes"
            db_alias = ALIAS

    fake = FakeEmbedder()
    pookiedb.embeddings(fake, dimensions=DIMS, batch_size=2, min_similarity=0.3)

    class Manual(pookiedb.Model):
        title = pookiedb.CharField(max_length=100)
        class Meta:
            db_table = "manuals"
            db_alias = ALIAS

    class Page(pookiedb.Model):
        manual  = pookiedb.ForeignKey(Manual, on_delete=pookiedb.CASCADE)
        number  = pookiedb.IntegerField()
        heading = pookiedb.CharField(max_length=100, searchable=True)
        content = pookiedb.TextField(searchable=True, null=True)
        class Meta:
            db_table = "pages"
            db_alias = ALIAS

    class Article(pookiedb.Model):
        id   = pookiedb.AutoUUIDField()
        body = pookiedb.TextField(searchable=True)
        class Meta:
            db_table = "articles"
            db_alias = ALIAS

    for model in (Note, Manual, Page, Article):
        model.create_table()

    class NS: pass
    ns = NS()
    ns.Note, ns.Manual, ns.Page, ns.Article, ns.fake = Note, Manual, Page, Article, fake
    yield ns
    embedder.reset()


@pytest.fixture(autouse=True)
def clean(m):
    for table in ("pages", "manuals", "notes", "articles"):
        pookiedb.execute(f'DELETE FROM "{table}"', alias=ALIAS)
    m.fake.calls.clear()
    m.fake.fail_with = None
    yield


def make_pages(m, manual_title="Store handbook"):
    manual = m.Manual.objects.create(title=manual_title)
    return manual, [
        m.Page.objects.create(manual=manual, number=i + 1, heading=h, content=c)
        for i, (h, c) in enumerate(DOCS)
    ]


def raw_row(table, pk):
    return pookiedb.execute(f'SELECT * FROM "{table}" WHERE "id" = ?', [pk], alias=ALIAS, fetch="one")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Embedding contract
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def contract(m):
    """Swap the callback for one test, then restore the module's fake."""
    saved = embedder.get_config()
    yield
    embedder._config = saved

@pytest.mark.parametrize("returned, message", [
    (None,                                  "returned None"),
    ("nope",                                "must return a list"),
    ([[0.0] * 3],                           "1 vectors for 2 texts"),
    ([[0.0] * 3, [0.0] * 2],                "has 2 dimensions"),
    ([[0.0] * 3, [0.0, float("nan"), 0.0]], "non-finite"),
    ([[0.0] * 3, [0.0, "x", 0.0]],          "non-numeric"),
    ([[0.0] * 3, "abc"],                    "must be a list of floats"),
])
def test_contract_violations_raise(contract, returned, message):
    pookiedb.embeddings(lambda texts: returned, dimensions=3)
    with pytest.raises(pookiedb.EmbeddingError, match=message):
        embedder.embed_texts(["a", "b"])

def test_contract_exception_is_wrapped(contract):
    boom = TimeoutError("provider down")
    def embed(texts): raise boom
    pookiedb.embeddings(embed, dimensions=3)
    with pytest.raises(pookiedb.EmbeddingError) as info:
        embedder.embed_texts(["a"])
    assert info.value.cause is boom

def test_contract_batches(contract):
    sizes = []
    pookiedb.embeddings(lambda t: sizes.append(len(t)) or [[1.0]] * len(t), dimensions=1, batch_size=2)
    assert len(embedder.embed_texts(list("abcde"))) == 5
    assert sizes == [2, 2, 1]

@pytest.mark.parametrize("kwargs", [{"embed": "not callable", "dimensions": 3}, {"embed": len, "dimensions": 0}])
def test_embeddings_config_validated(kwargs):
    with pytest.raises((TypeError, ValueError)):
        pookiedb.embeddings(kwargs["embed"], dimensions=kwargs["dimensions"])

def test_openai_compatible_client():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["auth"] = self.headers["Authorization"]
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            # Out of order on purpose: the client must sort by index
            data = [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]
            payload = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    embed = pookiedb.openai_compatible(
        f"http://127.0.0.1:{server.server_port}/v1/", api_key="sk-test", model="m1", dimensions=2
    )
    assert embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["path"] == "/v1/embeddings"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {"model": "m1", "input": ["a", "b"], "dimensions": 2}
    server.server_close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Searchable models & writes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_searchable_only_on_text_fields(m):
    with pytest.raises(pookiedb.FieldError, match="CharField and TextField"):
        type("BadSearch", (pookiedb.Model,), {
            "__module__": __name__, "n": pookiedb.IntegerField(searchable=True),
        })

def test_hidden_columns_only_with_embeddings(m):
    names = lambda model: [f.name for f in model._meta.fields]
    assert VECTOR_FIELD in names(m.Page)
    assert VECTOR_FIELD not in names(m.Note)
    assert m.Note._meta.search_fields and m.Note._meta.search_vector is None

def test_save_embeds_once(m):
    manual = m.Manual.objects.create(title="H")
    page = m.Page.objects.create(manual=manual, number=1, heading="Refunds", content="Money back")
    assert m.fake.calls == [["Refunds\n\nMoney back"]]
    row = raw_row("pages", page.id)
    assert row[HASH_FIELD] and row[VECTOR_FIELD] is not None and row[INDEXED_FIELD] == 0

    page.number = 2
    page.save()                      # text unchanged → no call
    assert len(m.fake.calls) == 1
    page.content = "Store credit only"
    page.save()                      # text changed → re-embed
    assert m.fake.calls[-1] == ["Refunds\n\nStore credit only"]

def test_fetch_and_save_keeps_embedding(m):
    _, pages = make_pages(m)
    fetched = m.Page.objects.get(pk=pages[0].id)
    assert fetched.__dict__[VECTOR_FIELD] is None       # never loaded
    fetched.number = 9
    fetched.save()
    assert raw_row("pages", fetched.id)[VECTOR_FIELD] is not None

def test_embedding_failure_writes_nothing(m):
    manual = m.Manual.objects.create(title="H")
    m.fake.fail_with = ConnectionError("provider down")
    with pytest.raises(pookiedb.EmbeddingError):
        m.Page.objects.create(manual=manual, number=1, heading="x", content="y")
    assert m.Page.objects.count() == 0

def test_empty_text_is_not_sent(m):
    manual = m.Manual.objects.create(title="H")
    m.Page.objects.create(manual=manual, number=1, heading="", content=None)
    assert m.fake.calls == []

def test_bulk_create_batches_embeddings(m):
    manual = m.Manual.objects.create(title="H")
    m.Page.objects.bulk_create([
        m.Page(manual=manual, number=i, heading=f"h{i}", content="c") for i in range(3)
    ])
    assert [len(c) for c in m.fake.calls] == [2, 1]      # batch_size=2
    assert pookiedb.execute(
        f'SELECT COUNT(*) AS c FROM "pages" WHERE "{VECTOR_FIELD}" IS NOT NULL', alias=ALIAS, fetch="one"
    )["c"] == 3

def test_bulk_update_marks_stale_then_embed_missing(m):
    _, pages = make_pages(m)
    m.Page.objects.filter(pk=pages[0].id).bulk_update(content="Store credit instead of money")
    assert raw_row("pages", pages[0].id)[VECTOR_FIELD] is None
    assert embed_missing(m.Page) == 1
    assert raw_row("pages", pages[0].id)[VECTOR_FIELD] is not None
    assert embed_missing(m.Page) == 0

def test_values_hide_search_columns(m):
    make_pages(m)
    row = m.Page.objects.values()[0]
    assert not any(k.startswith("_search") for k in row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Relation filters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_filter_across_fk(m):
    make_pages(m, "Store handbook")
    make_pages(m, "Staff guide")
    assert m.Page.objects.filter(manual__title="Staff guide").count() == 3
    assert m.Page.objects.filter(manual__title__icontains="store", number=1).count() == 1
    assert m.Page.objects.exclude(manual__title="Staff guide").count() == 3

def test_delete_across_fk(m):
    make_pages(m, "Store handbook")
    make_pages(m, "Staff guide")
    m.Page.objects.filter(manual__title="Staff guide").delete()
    assert m.Page.objects.count() == 3

def test_unknown_relation_lookup(m):
    with pytest.raises(pookiedb.FieldError, match="Unknown lookup"):
        list(m.Page.objects.filter(number__title="x"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Search
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_keyword_search(m):
    make_pages(m)
    results = list(m.Page.objects.search("refund policy", mode="keyword"))
    assert results[0].heading == "Refund policy"
    assert results[0].search_score == 1.0
    assert "**Refund**" in results[0].search_snippet
    assert m.fake.calls[-1] != ["refund policy"]          # keyword never embeds

def test_keyword_only_model_defaults_to_keyword(m):
    m.Note.objects.create(body="Refunds take 30 days")
    m.Note.objects.create(body="Nothing to see")
    assert [n.body for n in m.Note.objects.search("refunds")] == ["Refunds take 30 days"]
    with pytest.raises(pookiedb.FieldError, match="needs embeddings"):
        list(m.Note.objects.search("refunds", mode="semantic"))

def test_semantic_search_matches_meaning(m):
    make_pages(m)
    results = list(m.Page.objects.search("money back", mode="semantic"))
    assert results[0].heading == "Refund policy"
    assert 0 < results[0].search_score <= 1

def test_semantic_cutoff_means_no_results(m):
    make_pages(m)
    results = m.Page.objects.search("bananas", mode="semantic")
    calls_before = len(m.fake.calls)
    if not results:
        pass
    for _ in results:
        pass
    assert len(results) == 0
    assert len(m.fake.calls) == calls_before + 1          # `if` + loop embedded the query once

def test_hybrid_is_default_with_embeddings(m):
    make_pages(m)
    results = list(m.Page.objects.search("courier delivery"))
    assert results[0].heading == "Delivery times"
    assert all(0 < r.search_score <= 1 for r in results)
    assert results[0].search_snippet

def test_search_respects_filters_and_relations(m):
    make_pages(m, "Store handbook")
    make_pages(m, "Staff guide")
    results = list(m.Page.objects.filter(manual__title="Staff guide", number__in=[1, 2]).search("refund"))
    assert results and all(r.manual.title == "Staff guide" and r.number in (1, 2) for r in results)

def test_search_slicing_and_count(m):
    make_pages(m)
    qs = m.Page.objects.search("within", mode="keyword")
    assert qs.count() == 2
    assert len(qs[:1]) == 1
    assert qs[1].heading in ("Refund policy", "Delivery times")

def test_search_limited_fields(m):
    make_pages(m)
    assert list(m.Page.objects.search("password", mode="keyword", fields=["heading"])) == []
    assert len(m.Page.objects.search("password", mode="keyword", fields=["content"])) == 1
    with pytest.raises(pookiedb.FieldError, match="not searchable"):
        list(m.Page.objects.search("x", fields=["number"]))

def test_search_rejects_conflicting_calls(m):
    qs = m.Page.objects.search("refund")
    for call in (lambda: qs.order_by("number"), lambda: qs.values(), lambda: qs.delete(),
                 lambda: m.Page.objects.order_by("number").search("x")):
        with pytest.raises(pookiedb.FieldError):
            call()
    with pytest.raises(ValueError):
        m.Page.objects.search("x", mode="fuzzy")

def test_search_embedding_failure_raises(m):
    make_pages(m)
    m.fake.fail_with = RuntimeError("provider down")
    with pytest.raises(pookiedb.EmbeddingError):
        list(m.Page.objects.search("refund"))
    assert len(m.Page.objects.search("refund", mode="keyword")) == 1   # user's own fallback

def test_search_with_uuid_pk(m):
    for _, body in DOCS:
        m.Article.objects.create(body=body)
    results = list(m.Article.objects.search("money back"))
    assert isinstance(results[0].id, uuid.UUID) and "refund" in results[0].body

def test_like_escapes_wildcards(m):
    m.Note.objects.create(body="the user_id column")
    m.Note.objects.create(body="the userXid column")    # would match an unescaped "_"
    assert [n.body for n in m.Note.objects.search("user_id")] == ["the user_id column"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  zvec (file-based SQLite)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def zdb(m, tmp_path):
    alias = f"zvec_{uuid.uuid4().hex[:8]}"
    pookiedb.connect(f"sqlite:///{tmp_path / 'app.sqlite3'}", alias=alias)

    class ZDoc(pookiedb.Model):
        body = pookiedb.TextField(searchable=True)
        class Meta:
            db_table = "zdocs"
            db_alias = alias

    ZDoc.create_table()
    for _, body in DOCS:
        ZDoc.objects.create(body=body)
    return ZDoc, tmp_path / "app.sqlite3.zvec"

def test_zvec_matches_exact_search(zdb):
    ZDoc, root = zdb
    results = list(ZDoc.objects.search("money back", mode="semantic"))
    assert "refund" in results[0].body
    assert root.is_dir()
    pending = pookiedb.execute(
        f'SELECT COUNT(*) AS c FROM "zdocs" WHERE "{INDEXED_FIELD}" = 0',
        alias=ZDoc._meta.db_alias, fetch="one")["c"]
    assert pending == 0

def test_zvec_syncs_updates_and_ignores_deleted(zdb):
    ZDoc, _ = zdb
    list(ZDoc.objects.search("refund", mode="semantic"))          # initial sync
    refund = [d for d in ZDoc.objects.all() if "refund" in d.body][0]
    refund.delete()
    other = ZDoc.objects.first()
    other.body = "Get your money back fast"
    other.save()
    results = list(ZDoc.objects.search("money back", mode="semantic"))
    assert results[0].id == other.id
    assert all(r.id != refund.id for r in results)

def test_zvec_locked_falls_back_to_exact(zdb):
    import zvec
    ZDoc, root = zdb
    list(ZDoc.objects.search("refund", mode="semantic"))          # creates the collection
    ZDoc.objects.create(body="Refund requests go to billing")    # pending row
    held = zvec.open(path=zvec_store._collection_path(ZDoc))     # hold the write lock
    try:
        results = list(ZDoc.objects.search("refund billing", mode="semantic"))
        assert results[0].body == "Refund requests go to billing"
    finally:
        del held

def test_zvec_reindex(zdb):
    from pookiedb.search.backfill import reindex
    ZDoc, root = zdb
    list(ZDoc.objects.search("refund", mode="semantic"))
    reindex(ZDoc)
    assert not root.joinpath("zdocs").exists()
    assert "refund" in list(ZDoc.objects.search("money back", mode="semantic"))[0].body


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Migrations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_migrations_create_and_resize(m, tmp_path, contract):
    from pookiedb.migrations.runner import make_migrations
    from pookiedb.migrations.executor import migrate

    alias = f"mig_{uuid.uuid4().hex[:8]}"
    pookiedb.connect(f"sqlite:///{tmp_path / 'mig.sqlite3'}", alias=alias)
    mig_dir = str(tmp_path / "migrations")

    def define(dims):
        pookiedb.embeddings(lambda t: [fake_vector(x, dims) for x in t], dimensions=dims)
        return type("MigDoc", (pookiedb.Model,), {
            "__module__": __name__,
            "body": pookiedb.TextField(searchable=True),
            "Meta": type("Meta", (), {"db_table": "migdocs", "db_alias": alias}),
        })

    Doc = define(DIMS)
    first = open(make_migrations(mig_dir, db_alias=alias)).read()
    assert "migdocs_search_pending" in first and VECTOR_FIELD in first
    migrate(mig_dir, db_alias=alias)
    Doc.objects.create(body="refund")

    Doc = define(32)
    from pookiedb.db.registry import registry
    registry._models = {k: v for k, v in registry._models.items() if v is Doc or v.__name__ != "MigDoc"}
    second = open(make_migrations(mig_dir, db_alias=alias)).read()
    assert f'"{HASH_FIELD}" = NULL' in second
    migrate(mig_dir, db_alias=alias)
    assert embed_missing(Doc) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Vector column types
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.parametrize("dims, pg_type", [(1536, "vector(1536)"), (3072, "halfvec(3072)")])
def test_vector_column_type(dims, pg_type):
    from pookiedb.search.fields import VectorField
    field = VectorField(dims)
    assert field.sql_type("postgresql") == pg_type
    assert field.sql_type("sqlite") == "BLOB"

def test_vector_dimension_limit():
    from pookiedb.search.fields import VectorField
    with pytest.raises(pookiedb.FieldError, match="4000"):
        VectorField(5000).sql_type("postgresql")
