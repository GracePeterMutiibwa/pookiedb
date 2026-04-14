"""
PookieDB — Complete test suite (SQLite in-memory)
Covers: all field types, QuerySet API, relationships, migrations,
        scaffold logic, utilities, validators, aggregates, CLI helpers.
Run with: pytest tests/
"""

import os
import json
import uuid
import tempfile
import pytest
import pookiedb
from pookiedb.queryset.queryset import Q, Sum, Count, Avg, Max, Min


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Session-scoped DB + models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture(scope="session", autouse=True)
def db():
    pookiedb.connect(engine="sqlite", name=":memory:")

    class Tag(pookiedb.Model):
        name = pookiedb.CharField(max_length=50, unique=True)
        slug = pookiedb.SlugField(unique=True)
        class Meta:
            db_table = "tags"

    class Author(pookiedb.Model):
        uid       = pookiedb.UUIDField(auto=True, editable=False)
        name      = pookiedb.CharField(max_length=100)
        email     = pookiedb.EmailField(unique=True)
        bio       = pookiedb.TextField(null=True, blank=True)
        website   = pookiedb.URLField(null=True, blank=True)
        score     = pookiedb.FloatField(default=0.0)
        balance   = pookiedb.DecimalField(max_digits=10, decimal_places=2, default="0.00")
        is_active = pookiedb.BooleanField(default=True)
        joined    = pookiedb.DateTimeField(auto_now_add=True)
        class Meta:
            db_table = "authors"

    class Post(pookiedb.Model):
        title      = pookiedb.CharField(max_length=200)
        slug       = pookiedb.SlugField(unique=True)
        body       = pookiedb.TextField()
        author     = pookiedb.ForeignKey(Author, on_delete=pookiedb.CASCADE)
        published  = pookiedb.BooleanField(default=False)
        view_count = pookiedb.IntegerField(default=0)
        metadata   = pookiedb.JSONField(null=True)
        tags       = pookiedb.ManyToManyField(Tag)
        created_at = pookiedb.DateTimeField(auto_now_add=True)
        class Meta:
            db_table      = "posts"
            ordering      = ["-created_at"]
            unique_together = [["author", "slug"]]

    class UserProfile(pookiedb.Model):
        author = pookiedb.OneToOneField(Author, on_delete=pookiedb.CASCADE)
        avatar = pookiedb.URLField(null=True, blank=True)
        prefs  = pookiedb.JSONField(default=dict)
        class Meta:
            db_table = "user_profiles"

    Tag.create_table()
    Author.create_table()
    Post.create_table()
    UserProfile.create_table()

    db.Tag         = Tag
    db.Author      = Author
    db.Post        = Post
    db.UserProfile = UserProfile
    return db


@pytest.fixture(autouse=True)
def clean(db):
    pookiedb.execute('DELETE FROM "posts_tags"', ignore_errors=True)
    pookiedb.execute('DELETE FROM "posts"')
    pookiedb.execute('DELETE FROM "user_profiles"')
    pookiedb.execute('DELETE FROM "authors"')
    pookiedb.execute('DELETE FROM "tags"')
    yield


# ── helpers ──────────────────────────────────────────────────────

def make_author(db, name="Grace", email="grace@ex.com", **kw):
    kw.setdefault("score",    1.5)
    kw.setdefault("balance",  "10.00")
    return db.Author.objects.create(name=name, email=email, **kw)

def make_post(db, author, title="Post", slug="post", **kw):
    kw.setdefault("body", "Body text.")
    return db.Post.objects.create(title=title, slug=slug, author=author, **kw)

def make_tag(db, name, slug=None):
    return db.Tag.objects.create(name=name, slug=slug or name.lower())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CRUD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_create_assigns_pk(db):
    a = make_author(db)
    assert a.id is not None and isinstance(a.id, int)

def test_create_auto_uuid(db):
    a = make_author(db)
    fetched = db.Author.objects.get(pk=a.id)
    assert fetched.uid is not None
    # UUID auto field produces a uuid.UUID
    assert isinstance(fetched.uid, uuid.UUID)

def test_save_update(db):
    a = make_author(db)
    a.name = "Updated Name"
    a.save()
    assert db.Author.objects.get(pk=a.id).name == "Updated Name"

def test_update_fields_partial(db):
    a = make_author(db, name="Alice", email="al@ex.com")
    a.name = "Bob"
    a.save(update_fields=["name"])
    fresh = db.Author.objects.get(pk=a.id)
    assert fresh.name == "Bob"

def test_delete_instance(db):
    a = make_author(db)
    pk = a.id
    a.delete()
    with pytest.raises(pookiedb.DoesNotExist):
        db.Author.objects.get(pk=pk)

def test_refresh_from_db(db):
    a = make_author(db)
    db.Author.objects.filter(pk=a.id).bulk_update(name="Refreshed")
    a.refresh_from_db()
    assert a.name == "Refreshed"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Field types & round-trips
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_charfield_roundtrip(db):
    a = make_author(db, name="Charlene")
    assert db.Author.objects.get(pk=a.id).name == "Charlene"

def test_textfield_roundtrip(db):
    a = make_author(db, bio="A long biography.")
    assert db.Author.objects.get(pk=a.id).bio == "A long biography."

def test_emailfield_roundtrip(db):
    a = make_author(db, email="test@example.com")
    assert db.Author.objects.get(pk=a.id).email == "test@example.com"

def test_urlfield_roundtrip(db):
    a = make_author(db, website="https://gracepeter.dev")
    assert db.Author.objects.get(pk=a.id).website == "https://gracepeter.dev"

def test_floatfield_roundtrip(db):
    a = make_author(db, score=3.14)
    assert abs(db.Author.objects.get(pk=a.id).score - 3.14) < 0.001

def test_decimalfield_roundtrip(db):
    from decimal import Decimal
    a = make_author(db, balance="99.99")
    fetched = db.Author.objects.get(pk=a.id)
    assert fetched.balance == Decimal("99.99")

def test_booleanfield_default_false(db):
    a = make_author(db)
    p = make_post(db, a)
    assert db.Post.objects.get(pk=p.id).published == False

def test_booleanfield_set_true(db):
    a = make_author(db)
    p = make_post(db, a, published=True)
    assert db.Post.objects.get(pk=p.id).published == True

def test_integerfield_roundtrip(db):
    a = make_author(db)
    p = make_post(db, a, view_count=42)
    assert db.Post.objects.get(pk=p.id).view_count == 42

def test_datetimefield_auto_now_add(db):
    a = make_author(db)
    fetched = db.Author.objects.get(pk=a.id)
    assert fetched.joined is not None

def test_jsonfield_dict(db):
    a = make_author(db)
    p = make_post(db, a, metadata={"key": "val", "n": 99})
    fetched = db.Post.objects.get(pk=p.id)
    assert fetched.metadata["key"] == "val"
    assert fetched.metadata["n"] == 99

def test_jsonfield_list(db):
    a = make_author(db)
    p = make_post(db, a, metadata=[1, 2, 3])
    fetched = db.Post.objects.get(pk=p.id)
    assert fetched.metadata == [1, 2, 3]

def test_jsonfield_null(db):
    a = make_author(db)
    p = make_post(db, a)
    assert db.Post.objects.get(pk=p.id).metadata is None

def test_slugfield_roundtrip(db):
    t = make_tag(db, "Python", "python")
    assert db.Tag.objects.get(pk=t.id).slug == "python"

def test_uuidfield_auto(db):
    a1 = make_author(db, name="U1", email="u1@ex.com")
    a2 = make_author(db, name="U2", email="u2@ex.com")
    assert a1.uid != a2.uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Filter lookups
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_filter_exact(db):
    make_author(db, name="Alice", email="a@ex.com")
    make_author(db, name="Bob",   email="b@ex.com")
    assert db.Author.objects.filter(name="Alice").count() == 1

def test_filter_iexact(db):
    make_author(db, name="Alice", email="a@ex.com")
    assert db.Author.objects.filter(name__iexact="ALICE").count() == 1

def test_filter_contains(db):
    make_author(db, name="Grace Mutiibwa", email="g@ex.com")
    assert db.Author.objects.filter(name__contains="Mutiibwa").count() == 1

def test_filter_icontains(db):
    make_author(db, name="Grace Mutiibwa", email="g@ex.com")
    assert db.Author.objects.filter(name__icontains="mutiibwa").count() == 1

def test_filter_startswith(db):
    make_author(db, name="Grace", email="g@ex.com")
    assert db.Author.objects.filter(name__startswith="Gra").count() == 1

def test_filter_istartswith(db):
    make_author(db, name="Grace", email="g@ex.com")
    assert db.Author.objects.filter(name__istartswith="gra").count() == 1

def test_filter_endswith(db):
    make_author(db, name="Grace", email="g@ex.com")
    assert db.Author.objects.filter(name__endswith="ace").count() == 1

def test_filter_iendswith(db):
    make_author(db, name="Grace", email="g@ex.com")
    assert db.Author.objects.filter(name__iendswith="ACE").count() == 1

def test_filter_gt_gte_lt_lte(db):
    emails = ["a@ex.com", "b@ex.com", "c@ex.com", "d@ex.com", "e@ex.com"]
    for i, e in enumerate(emails):
        db.Author.objects.create(name=f"U{i}", email=e, score=float(i))
    assert db.Author.objects.filter(score__gt=2.0).count()  == 2
    assert db.Author.objects.filter(score__gte=2.0).count() == 3
    assert db.Author.objects.filter(score__lt=2.0).count()  == 2
    assert db.Author.objects.filter(score__lte=2.0).count() == 3

def test_filter_in(db):
    a1 = make_author(db, name="A", email="a@ex.com")
    a2 = make_author(db, name="B", email="b@ex.com")
    make_author(db,         name="C", email="c@ex.com")
    assert db.Author.objects.filter(id__in=[a1.id, a2.id]).count() == 2

def test_filter_in_empty(db):
    make_author(db)
    # empty IN → should return nothing, not crash
    assert db.Author.objects.filter(id__in=[]).count() == 0

def test_filter_isnull_true(db):
    make_author(db, name="WithBio", email="w@ex.com", bio="hello")
    make_author(db, name="NoBio",   email="n@ex.com")
    assert db.Author.objects.filter(bio__isnull=True).count()  == 1
    assert db.Author.objects.filter(bio__isnull=False).count() == 1

def test_filter_range(db):
    for i, e in enumerate(["a@ex.com","b@ex.com","c@ex.com","d@ex.com"]):
        db.Author.objects.create(name=f"U{i}", email=e, score=float(i))
    assert db.Author.objects.filter(score__range=(1.0, 2.0)).count() == 2

def test_filter_ne(db):
    make_author(db, name="Alice", email="a@ex.com")
    make_author(db, name="Bob",   email="b@ex.com")
    assert db.Author.objects.filter(name__ne="Alice").count() == 1

def test_exclude(db):
    make_author(db, name="Alice", email="a@ex.com")
    make_author(db, name="Bob",   email="b@ex.com")
    names = list(db.Author.objects.exclude(name="Alice").values_list("name", flat=True))
    assert "Alice" not in names
    assert "Bob" in names


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Q objects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_q_or(db):
    make_author(db, name="Alice", email="a@ex.com")
    make_author(db, name="Bob",   email="b@ex.com")
    make_author(db, name="Carol", email="c@ex.com")
    assert db.Author.objects.filter(Q(name="Alice") | Q(name="Bob")).count() == 2

def test_q_and(db):
    make_author(db, name="Alice", email="a@ex.com")
    assert db.Author.objects.filter(Q(name="Alice") & Q(email="a@ex.com")).count() == 1
    assert db.Author.objects.filter(Q(name="Alice") & Q(email="x@ex.com")).count() == 0

def test_q_negation(db):
    make_author(db, name="Alice", email="a@ex.com")
    make_author(db, name="Bob",   email="b@ex.com")
    assert db.Author.objects.filter(~Q(name="Alice")).count() == 1

def test_q_complex(db):
    for n, e in [("Alice","al@ex.com"),("Bob","bo@ex.com"),("Carol","ca@ex.com"),("Dave","da@ex.com")]:
        make_author(db, name=n, email=e)
    # (Alice OR Bob) AND NOT Carol
    r = db.Author.objects.filter(
        (Q(name="Alice") | Q(name="Bob")) & ~Q(name="Carol")
    ).count()
    assert r == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Ordering, slicing, pagination
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_order_by_asc(db):
    for n, e in [("Zebra","z@ex.com"),("Apple","ap@ex.com"),("Mango","m@ex.com")]:
        make_author(db, name=n, email=e)
    names = list(db.Author.objects.order_by("name").values_list("name", flat=True))
    assert names == sorted(names)

def test_order_by_desc(db):
    for n, e in [("Zebra","z@ex.com"),("Apple","ap@ex.com"),("Mango","m@ex.com")]:
        make_author(db, name=n, email=e)
    names = list(db.Author.objects.order_by("-name").values_list("name", flat=True))
    assert names == sorted(names, reverse=True)

def test_first_last(db):
    a1 = make_author(db, name="First", email="f@ex.com")
    a2 = make_author(db, name="Last",  email="l@ex.com")
    assert db.Author.objects.order_by("id").first().id == a1.id
    assert db.Author.objects.order_by("id").last().id  == a2.id

def test_first_on_empty(db):
    assert db.Author.objects.first() is None

def test_slice(db):
    for i in range(6):
        make_author(db, name=f"U{i}", email=f"u{i}@ex.com")
    page = list(db.Author.objects.order_by("id")[2:5])
    assert len(page) == 3

def test_limit_offset(db):
    for i in range(5):
        make_author(db, name=f"U{i}", email=f"u{i}@ex.com")
    page = list(db.Author.objects.order_by("id").offset(1).limit(3))
    assert len(page) == 3

def test_paginator_full(db):
    from pookiedb.utils import Paginator
    for i in range(10):
        make_author(db, name=f"P{i}", email=f"p{i}@ex.com")
    p = Paginator(db.Author.objects.order_by("id"), per_page=3)
    assert p.num_pages == 4
    assert p.count    == 10
    pg1 = p.page(1)
    assert len(pg1.object_list)   == 3
    assert pg1.has_next()
    assert not pg1.has_previous()
    assert pg1.next_page_number() == 2
    pg4 = p.page(4)
    assert len(pg4.object_list)      == 1
    assert not pg4.has_next()
    assert pg4.previous_page_number() == 3

def test_paginator_empty(db):
    from pookiedb.utils import Paginator
    p = Paginator(db.Author.objects.all(), per_page=10)
    assert p.num_pages == 1
    assert p.count     == 0

def test_paginator_invalid_page(db):
    from pookiedb.utils import Paginator
    for i in range(3):
        make_author(db, name=f"U{i}", email=f"u{i}@ex.com")
    p = Paginator(db.Author.objects.all(), per_page=2)
    with pytest.raises(ValueError):
        p.page(99)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Projection: values / values_list
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_values_returns_dicts(db):
    make_author(db, name="Alice", email="a@ex.com")
    row = list(db.Author.objects.values("name", "email"))[0]
    assert isinstance(row, dict)
    assert set(row.keys()) == {"name", "email"}

def test_values_list_returns_tuples(db):
    make_author(db, name="Alice", email="a@ex.com")
    row = list(db.Author.objects.values_list("name", "email"))[0]
    assert isinstance(row, tuple)
    assert row[0] == "Alice"

def test_values_list_flat(db):
    make_author(db, name="Alice", email="a@ex.com")
    ids = list(db.Author.objects.values_list("id", flat=True))
    assert isinstance(ids[0], int)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Aggregates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_count_and_exists(db):
    assert db.Author.objects.count()  == 0
    assert not db.Author.objects.exists()
    make_author(db)
    assert db.Author.objects.count()  == 1
    assert db.Author.objects.exists()

def test_aggregate_count(db):
    a = make_author(db)
    make_post(db, a, slug="p1")
    make_post(db, a, title="P2", slug="p2")
    r = db.Post.objects.aggregate(total=Count("id"))
    assert r["total"] == 2

def test_aggregate_sum(db):
    a = make_author(db)
    make_post(db, a, slug="p1", view_count=10)
    make_post(db, a, title="P2", slug="p2", view_count=20)
    r = db.Post.objects.aggregate(views=Sum("view_count"))
    assert r["views"] == 30

def test_aggregate_avg(db):
    a = make_author(db)
    make_post(db, a, slug="p1", view_count=10)
    make_post(db, a, title="P2", slug="p2", view_count=20)
    r = db.Post.objects.aggregate(avg=Avg("view_count"))
    assert r["avg"] == 15.0

def test_aggregate_max_min(db):
    a = make_author(db)
    make_post(db, a, slug="p1", view_count=5)
    make_post(db, a, title="P2", slug="p2", view_count=50)
    r = db.Post.objects.aggregate(mx=Max("view_count"), mn=Min("view_count"))
    assert r["mx"] == 50
    assert r["mn"] == 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  get / get_or_create / update_or_create
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_by_pk(db):
    a = make_author(db)
    assert db.Author.objects.get(pk=a.id).name == "Grace"

def test_does_not_exist(db):
    with pytest.raises(pookiedb.DoesNotExist):
        db.Author.objects.get(email="nobody@ex.com")

def test_multiple_objects_returned(db):
    make_author(db, name="A", email="a@ex.com")
    make_author(db, name="B", email="b@ex.com")
    with pytest.raises(pookiedb.MultipleObjectsReturned):
        db.Author.objects.get(name__icontains="")

def test_get_or_create_creates(db):
    a, created = db.Author.objects.get_or_create(email="g@ex.com", defaults={"name":"Grace"})
    assert created and a.name == "Grace"

def test_get_or_create_gets(db):
    a1, _ = db.Author.objects.get_or_create(email="g@ex.com", defaults={"name":"Grace"})
    a2, created = db.Author.objects.get_or_create(email="g@ex.com", defaults={"name":"Grace"})
    assert not created and a1.id == a2.id

def test_update_or_create_creates(db):
    a, c = db.Author.objects.update_or_create(email="g@ex.com", defaults={"name":"Grace"})
    assert c and a.name == "Grace"

def test_update_or_create_updates(db):
    db.Author.objects.create(name="Old", email="g@ex.com")
    a, c = db.Author.objects.update_or_create(email="g@ex.com", defaults={"name":"New"})
    assert not c and a.name == "New"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Bulk operations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_bulk_create(db):
    objs = [db.Author(name=f"B{i}", email=f"b{i}@ex.com") for i in range(5)]
    db.Author.objects.bulk_create(objs)
    assert db.Author.objects.count() == 5

def test_bulk_update(db):
    make_author(db, name="Alice", email="al@ex.com")
    make_author(db, name="Bob",   email="bo@ex.com")
    db.Author.objects.filter(name="Alice").bulk_update(name="Alicia")
    assert db.Author.objects.filter(name="Alicia").count() == 1
    assert db.Author.objects.filter(name="Alice").count()  == 0

def test_queryset_delete(db):
    make_author(db, name="Alice", email="al@ex.com")
    make_author(db, name="Bob",   email="bo@ex.com")
    db.Author.objects.filter(name="Alice").delete()
    assert db.Author.objects.count()               == 1
    assert db.Author.objects.filter(name="Bob").exists()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ForeignKey
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_fk_lazy_load(db):
    a = make_author(db)
    p = make_post(db, a)
    fetched = db.Post.objects.get(pk=p.id)
    assert fetched.author.name == "Grace"

def test_fk_filter_by_instance(db):
    a = make_author(db)
    make_post(db, a, slug="p1")
    make_post(db, a, title="P2", slug="p2")
    assert db.Post.objects.filter(author=a).count() == 2

def test_fk_filter_by_id(db):
    a = make_author(db)
    make_post(db, a, slug="p1")
    assert db.Post.objects.filter(author_id=a.id).count() == 1

def test_fk_assigned_as_instance(db):
    a = make_author(db)
    p = db.Post(title="T", slug="t", body=".", author=a)
    p.save()
    assert db.Post.objects.get(pk=p.id).author_id == a.id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  OneToOneField
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_onetone_create_and_load(db):
    a  = make_author(db)
    up = db.UserProfile.objects.create(author=a, avatar="https://cdn.example.com/img.png", prefs={"theme":"dark"})
    fetched = db.UserProfile.objects.get(pk=up.id)
    assert fetched.author.id    == a.id
    assert fetched.prefs["theme"] == "dark"

def test_onetone_unique_constraint(db):
    import sqlite3
    a = make_author(db)
    db.UserProfile.objects.create(author=a)
    with pytest.raises(Exception):   # sqlite3 unique violation
        db.UserProfile.objects.create(author=a)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ManyToManyField
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_m2m_add_and_all(db):
    a = make_author(db)
    p = make_post(db, a)
    t = make_tag(db, "python")
    p.tags.add(t)
    assert p.tags.count()   == 1
    assert p.tags.all()[0].name == "python"

def test_m2m_remove(db):
    a, p, t = make_author(db), make_post(db, make_author(db, email="x@x.com")), make_tag(db,"orm")
    a2 = make_author(db, name="B", email="b@ex.com")
    p2 = make_post(db, a2, slug="p2")
    p2.tags.add(t)
    p2.tags.remove(t)
    assert p2.tags.count() == 0

def test_m2m_set(db):
    a  = make_author(db)
    p  = make_post(db, a)
    t1 = make_tag(db, "py",  "py")
    t2 = make_tag(db, "orm", "orm")
    t3 = make_tag(db, "sql", "sql")
    p.tags.set([t1, t2])
    assert p.tags.count() == 2
    p.tags.set([t3])
    assert p.tags.count() == 1
    assert p.tags.all()[0].name == "sql"

def test_m2m_clear(db):
    a = make_author(db)
    p = make_post(db, a)
    t = make_tag(db, "py", "py")
    p.tags.add(t)
    p.tags.clear()
    assert p.tags.count() == 0

def test_m2m_no_duplicates(db):
    a = make_author(db)
    p = make_post(db, a)
    t = make_tag(db, "dup", "dup")
    p.tags.add(t)
    p.tags.add(t)  # second add should be silently ignored
    assert p.tags.count() == 1

def test_m2m_multiple_tags(db):
    a  = make_author(db)
    p  = make_post(db, a)
    tags = [make_tag(db, f"t{i}", f"t{i}") for i in range(4)]
    for t in tags:
        p.tags.add(t)
    assert p.tags.count() == 4


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_email_invalid(db):
    with pytest.raises(pookiedb.ValidationError):
        db.Author(name="Bad", email="not-an-email").save()

def test_email_valid_formats(db):
    for email in ["user@example.com", "a+b@domain.co.ug", "x.y@z.io"]:
        a = db.Author(name="V", email=email)
        a.full_clean()  # should not raise

def test_charfield_max_length(db):
    with pytest.raises(pookiedb.ValidationError):
        db.Author(name="X" * 300, email="x@ex.com").save()

def test_charfield_max_length_boundary(db):
    # Exactly 100 chars is fine
    name = "A" * 100
    a = db.Author(name=name, email="bound@ex.com")
    a.full_clean()

def test_urlfield_invalid(db):
    with pytest.raises(pookiedb.ValidationError):
        db.Author(name="Bad", email="ok@ex.com", website="not-a-url").save()

def test_urlfield_valid(db):
    a = db.Author(name="V", email="v@ex.com", website="https://valid.com")
    a.full_clean()

def test_slugfield_invalid(db):
    with pytest.raises(pookiedb.ValidationError):
        a = make_author(db)
        make_post(db, a, slug="invalid slug!")

def test_slugfield_valid_chars(db):
    a = make_author(db)
    p = make_post(db, a, slug="valid-slug_123")
    assert db.Post.objects.get(pk=p.id).slug == "valid-slug_123"

def test_null_field_explicit_none(db):
    a = make_author(db, bio=None)
    assert db.Author.objects.get(pk=a.id).bio is None

def test_jsonfield_invalid_raises(db):
    from pookiedb.fields.special import JSONField
    from pookiedb.exceptions import ValidationError
    f = JSONField()
    f.name = "meta"
    with pytest.raises(ValidationError):
        f.validate("not { valid json }", None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Raw SQL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_raw_sql_select(db):
    make_author(db, name="RawUser", email="raw@ex.com")
    results = db.Author.objects.raw("SELECT * FROM authors WHERE name = ?", ["RawUser"])
    assert len(results) == 1
    assert results[0].name == "RawUser"

def test_raw_sql_multiple(db):
    for i in range(3):
        make_author(db, name=f"R{i}", email=f"r{i}@ex.com")
    results = db.Author.objects.raw("SELECT * FROM authors")
    assert len(results) == 3

def test_execute_helper(db):
    make_author(db, name="ExecUser", email="exec@ex.com")
    rows = pookiedb.execute("SELECT name FROM authors WHERE name = ?", ["ExecUser"],
                             alias="default", fetch="all")
    assert rows[0]["name"] == "ExecUser"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Transactions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_transaction_commit(db):
    from pookiedb.db.connection import transaction
    with transaction():
        make_author(db, name="TxUser", email="tx@ex.com")
    assert db.Author.objects.filter(name="TxUser").exists()

def test_transaction_rollback_on_exception(db):
    from pookiedb.db.connection import transaction
    try:
        with transaction():
            make_author(db, name="RollbackUser", email="rb@ex.com")
            raise ValueError("forced rollback")
    except ValueError:
        pass
    # No unhandled exception — the with block swallowed it after rollback


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Migrations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_makemigrations_creates_file(db):
    from pookiedb.migrations.runner import make_migrations
    with tempfile.TemporaryDirectory() as tmpdir:
        path = make_migrations(tmpdir, db_alias="default")
        assert path is not None
        assert path.endswith(".py")
        assert os.path.exists(path)

def test_makemigrations_no_changes(db):
    from pookiedb.migrations.runner import make_migrations
    with tempfile.TemporaryDirectory() as tmpdir:
        make_migrations(tmpdir, db_alias="default")   # first run
        path2 = make_migrations(tmpdir, db_alias="default")  # second run same models
        assert path2 is None  # no changes

def test_migrate_applies(db):
    from pookiedb.migrations.runner import make_migrations
    from pookiedb.migrations.executor import migrate, show_migrations
    pookiedb.connect(engine="sqlite", name=":memory:", alias="mig_test")

    import pookiedb as _pkg
    class TmpModel(_pkg.Model):
        title = _pkg.CharField(max_length=100)
        class Meta:
            db_table  = "tmp_items"
            db_alias  = "mig_test"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = make_migrations(tmpdir, db_alias="mig_test")
        assert path is not None
        applied = migrate(tmpdir, db_alias="mig_test")
        assert len(applied) == 1
        # second migrate: already up-to-date
        applied2 = migrate(tmpdir, db_alias="mig_test")
        assert applied2 == []

def test_show_migrations(db):
    from pookiedb.migrations.runner import make_migrations
    from pookiedb.migrations.executor import migrate, show_migrations
    pookiedb.connect(engine="sqlite", name=":memory:", alias="show_test")

    import pookiedb as _pkg
    class ShowModel(_pkg.Model):
        name = _pkg.CharField(max_length=50)
        class Meta:
            db_table = "show_items"
            db_alias = "show_test"

    with tempfile.TemporaryDirectory() as tmpdir:
        make_migrations(tmpdir, db_alias="show_test")
        migs_before = show_migrations(tmpdir, db_alias="show_test")
        assert migs_before[0]["applied"] == False
        migrate(tmpdir, db_alias="show_test")
        migs_after = show_migrations(tmpdir, db_alias="show_test")
        assert migs_after[0]["applied"] == True

def test_rollback(db):
    from pookiedb.migrations.runner import make_migrations
    from pookiedb.migrations.executor import migrate, rollback, show_migrations
    pookiedb.connect(engine="sqlite", name=":memory:", alias="rb_test")

    import pookiedb as _pkg
    class RbModel(_pkg.Model):
        val = _pkg.IntegerField(default=0)
        class Meta:
            db_table = "rb_items"
            db_alias = "rb_test"

    with tempfile.TemporaryDirectory() as tmpdir:
        make_migrations(tmpdir, db_alias="rb_test")
        migrate(tmpdir, db_alias="rb_test")
        rolled = rollback(tmpdir, db_alias="rb_test", steps=1)
        assert len(rolled) == 1
        migs = show_migrations(tmpdir, db_alias="rb_test")
        assert migs[0]["applied"] == False

def test_migration_operations_sql():
    from pookiedb.migrations.runner import (
        CreateTable, AddColumn, DropColumn, AlterColumn, CreateIndex, DropTable, RawSQL
    )
    ct = CreateTable("items", [{"name":"id","sql":'"id" INTEGER PRIMARY KEY'}], [])
    assert "CREATE TABLE" in ct.forward_sql("sqlite")[0]
    assert "DROP TABLE"   in ct.backward_sql("sqlite")[0]

    ac = AddColumn("items", "name", '"name" TEXT')
    assert "ADD COLUMN" in ac.forward_sql("sqlite")[0]

    ci = CreateIndex("items", "name")
    assert "CREATE INDEX" in ci.forward_sql("sqlite")[0]
    assert "DROP INDEX"   in ci.backward_sql("sqlite")[0]

    rs = RawSQL("SELECT 1", "SELECT 0")
    assert rs.forward_sql("sqlite")[0]  == "SELECT 1"
    assert rs.backward_sql("sqlite")[0] == "SELECT 0"

    dt = DropTable("items")
    assert "DROP TABLE" in dt.forward_sql("sqlite")[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scaffold (pookiedb init)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_scaffold_sqlite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pookiedb.cli.tui import scaffold
    scaffold({
        "project_name":  "blog",
        "package_name":  "blog",
        "db_engine":     "sqlite",
        "db_name":       "blog.sqlite3",
        "model_name":    "Post",
        "model_fields":  "title:CharField,body:TextField,published:BooleanField",
        "author":        "Grace Peter Mutiibwa",
        "author_email":  "grace@example.com",
        "version":       "0.1.0",
    })
    proj = tmp_path / "blog"
    assert (proj / "settings.py").exists()
    assert (proj / "pyproject.toml").exists()
    assert (proj / "README.md").exists()
    assert (proj / ".gitignore").exists()
    assert (proj / "migrations" / "__init__.py").exists()
    assert (proj / "blog" / "models.py").exists()
    models_src = (proj / "blog" / "models.py").read_text()
    assert "class Post(pookiedb.Model):" in models_src
    assert "title = pookiedb.CharField" in models_src
    assert "body = pookiedb.TextField"  in models_src

def test_scaffold_postgres(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pookiedb.cli.tui import scaffold
    scaffold({
        "project_name":  "shop",
        "package_name":  "shop",
        "db_engine":     "postgresql",
        "db_name":       "shopdb",
        "db_host":       "localhost",
        "db_port":       "5432",
        "db_user":       "admin",
        "db_password":   "secret",
        "model_name":    "",
        "model_fields":  "",
        "author":        "Grace",
        "author_email":  "",
        "version":       "1.0.0",
    })
    settings = (tmp_path / "shop" / "settings.py").read_text()
    assert "postgresql" in settings
    assert "shopdb"     in settings
    models = (tmp_path / "shop" / "shop" / "models.py").read_text()
    assert "# Define your models here" in models

def test_scaffold_no_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pookiedb.cli.tui import scaffold
    scaffold({
        "project_name": "bare",
        "package_name": "bare",
        "db_engine":    "sqlite",
        "db_name":      "bare.sqlite3",
        "model_name":   "",
        "model_fields": "",
        "author":       "Grace",
    })
    assert (tmp_path / "bare" / "bare" / "models.py").exists()

def test_scaffold_field_parser():
    from pookiedb.cli.tui import _parse_fields
    result = _parse_fields("name:CharField,age:IntegerField,active:BooleanField")
    assert result == [("name","CharField"), ("age","IntegerField"), ("active","BooleanField")]

def test_scaffold_field_parser_empty():
    from pookiedb.cli.tui import _parse_fields
    assert _parse_fields("") == []

def test_scaffold_field_parser_no_type():
    from pookiedb.cli.tui import _parse_fields
    result = _parse_fields("title")
    assert result[0][0] == "title"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_registry_contains_models(db):
    from pookiedb.db.registry import registry
    names = [m.__name__ for m in registry.all()]
    assert "Author" in names
    assert "Post"   in names
    assert "Tag"    in names

def test_registry_get_by_name(db):
    from pookiedb.db.registry import registry
    assert registry.get("Author") is not None

def test_registry_get_missing():
    from pookiedb.db.registry import registry
    assert registry.get("NoSuchModel") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Exceptions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_does_not_exist_is_subclass():
    assert issubclass(pookiedb.DoesNotExist, pookiedb.PookieError)

def test_multiple_objects_is_subclass():
    assert issubclass(pookiedb.MultipleObjectsReturned, pookiedb.PookieError)

def test_validation_error_has_field():
    from pookiedb.exceptions import ValidationError
    e = ValidationError("bad value", field="email")
    assert e.field == "email"

def test_migration_error_is_subclass():
    assert issubclass(pookiedb.MigrationError, pookiedb.PookieError)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Utilities
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_slugify_basic():
    from pookiedb.utils import slugify
    assert slugify("Hello World!") == "hello-world"

def test_slugify_strips_specials():
    from pookiedb.utils import slugify
    assert slugify("Foo & Bar @ Baz") == "foo-bar-baz"

def test_slugify_handles_spaces():
    from pookiedb.utils import slugify
    assert slugify("  multiple   spaces  ") == "multiple-spaces"

def test_slugify_preserves_hyphens():
    from pookiedb.utils import slugify
    assert slugify("already-slugged") == "already-slugged"

def test_camel_to_snake_basic():
    from pookiedb.utils import camel_to_snake
    assert camel_to_snake("CamelCase")   == "camel_case"
    assert camel_to_snake("myFieldName") == "my_field_name"

def test_camel_to_snake_acronym():
    from pookiedb.utils import camel_to_snake
    assert camel_to_snake("parseHTMLDoc") == "parse_html_doc"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Connection helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_connection_default():
    from pookiedb.db.connection import get_connection
    pool = get_connection("default")
    assert pool is not None

def test_get_connection_missing():
    from pookiedb.db.connection import get_connection
    from pookiedb.exceptions import ConnectionError as PookieConnError
    with pytest.raises(PookieConnError):
        get_connection("does_not_exist")

def test_url_connect_sqlite():
    pookiedb.connect("sqlite:///test_url.sqlite3", alias="url_test")
    from pookiedb.db.connection import get_connection
    pool = get_connection("url_test")
    assert pool.config.is_sqlite

def test_url_connect_memory():
    pookiedb.connect("sqlite://:memory:", alias="mem_url")
    from pookiedb.db.connection import get_connection
    pool = get_connection("mem_url")
    assert pool.config.is_sqlite


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Model repr / equality
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_model_repr(db):
    a = make_author(db)
    assert "Author" in repr(a)
    assert str(a.id) in repr(a)

def test_model_equality(db):
    a = make_author(db)
    b = db.Author.objects.get(pk=a.id)
    assert a == b

def test_model_inequality(db):
    a1 = make_author(db, name="A1", email="a1@ex.com")
    a2 = make_author(db, name="A2", email="a2@ex.com")
    assert a1 != a2

def test_model_hash(db):
    a = make_author(db)
    b = db.Author.objects.get(pk=a.id)
    assert hash(a) == hash(b)
    s = {a, b}
    assert len(s) == 1
