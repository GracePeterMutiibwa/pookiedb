# PookieDB ORM

A Django-style Python ORM for **PostgreSQL** and **SQLite** - with auto migrations, relationships, a chainable QuerySet API, and a friendly CLI.

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation Status](https://readthedocs.org/projects/pookiedb/badge/?version=latest)](https://pookiedb.readthedocs.io/en/latest/?badge=latest)

## Installation

```bash
pip install pookiedb
# PostgreSQL support is included via psycopg2-binary, SQLite vector search via zvec
```

Needs Python 3.10+ on Linux (glibc 2.28+ or musl, so any host from roughly 2019 on), macOS on Apple Silicon, or Windows x64.
On AWS Lambda, use a Python 3.12+ runtime. See [Installation](https://pookiedb.readthedocs.io/en/latest/#installation) for the hosts that aren't supported.

---

## Quick Start

### 1. Connect to your database

```python
import pookiedb

# SQLite (relative path; use four slashes for an absolute one: sqlite:////var/data/mydb.sqlite3)
pookiedb.connect("sqlite:///mydb.sqlite3")

# PostgreSQL
pookiedb.connect("postgresql://postgres:password@localhost:5432/mydb")

# Or using kwargs
pookiedb.connect(engine="postgresql", name="mydb", host="localhost", user="postgres", password="secret")
```

### 2. Define models

```python
import pookiedb

class Author(pookiedb.Model):
    name = pookiedb.CharField(max_length=100)
    email = pookiedb.EmailField(unique=True)
    bio = pookiedb.TextField(null=True, blank=True)
    joined = pookiedb.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "authors"
        ordering = ["-joined"]


class Post(pookiedb.Model):
    title = pookiedb.CharField(max_length=200)
    slug = pookiedb.SlugField(unique=True)
    body = pookiedb.TextField()
    author = pookiedb.ForeignKey(Author, on_delete=pookiedb.CASCADE)
    published = pookiedb.BooleanField(default=False)
    metadata = pookiedb.JSONField(null=True)
    tags = pookiedb.ManyToManyField("Tag", related_name="posts")
    created_at = pookiedb.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "posts"


class Tag(pookiedb.Model):
    name = pookiedb.CharField(max_length=50, unique=True)

    class Meta:
        db_table = "tags"
```

### 3. Create tables

```python
Author.create_table()
Tag.create_table()
Post.create_table()
```

---

## QuerySet API

Pookie's QuerySet is **lazy** and **chainable** - queries only hit the database when you iterate or evaluate.

```python
# Create
alice = Author.objects.create(name="Alice", email="alice@example.com")

# Get
author = Author.objects.get(email="alice@example.com")

# Filter (chained)
posts = (
    Post.objects
    .filter(published=True)
    .filter(author=alice)
    .order_by("-created_at")
    .limit(10)
)

# Exclude
drafts = Post.objects.exclude(published=True)

# Q objects for OR / AND / NOT logic
from pookiedb.queryset.queryset import Q

results = Post.objects.filter(
    Q(title__icontains="python") | Q(author__name="Alice")
)

# get_or_create / update_or_create
tag, created = Tag.objects.get_or_create(name="django")

# Slicing
first_five = Post.objects.all()[:5]

# values / values_list
names = Author.objects.values("name", "email")
ids = Post.objects.values_list("id", flat=True)

# Aggregates
from pookiedb.queryset.queryset import Sum, Avg, Count
stats = Post.objects.aggregate(total=Count("id"), avg_len=Avg("id"))

# Bulk create
posts = Post.objects.bulk_create([
    Post(title="Post 1", slug="post-1", body="...", author=alice),
    Post(title="Post 2", slug="post-2", body="...", author=alice),
])

# Raw SQL
authors = Author.objects.raw("SELECT * FROM authors WHERE name ILIKE %s", ["%ali%"])

# Delete
Post.objects.filter(published=False).delete()

# Bulk update
Post.objects.filter(author=alice).bulk_update(published=True)
```

---

## Relationships

### ForeignKey

```python
post = Post.objects.get(id=1)
print(post.author)       # lazy-loads the Author
print(post.author.name)
```

### ManyToMany

```python
post = Post.objects.get(id=1)
tag = Tag.objects.get(name="python")

post.tags.add(tag)
post.tags.all()     # QuerySet of related Tags
post.tags.remove(tag)
post.tags.set([tag1, tag2])
post.tags.clear()
post.tags.count()
```

### OneToOne

```python
class UserProfile(pookiedb.Model):
    user = pookiedb.OneToOneField("User", on_delete=pookiedb.CASCADE)
    avatar_url = pookiedb.URLField(null=True)
```

---

## Search

Mark text fields as searchable and call `.search()`:

```python
class Article(pookiedb.Model):
    title = pookiedb.CharField(max_length=200, searchable=True)
    body = pookiedb.TextField(searchable=True)

for a in Article.objects.filter(author__name="Alice").search("refund policy")[:5]:
    print(a.title, a.search_score, a.search_snippet)   # score 0–1, snippet with **bold** matches
```

That's keyword search. Register an embedding callback (before defining models) to add semantic
search. `.search()` then combines both:

```python
def my_embed(texts: list[str]) -> list[list[float]]:
    ...  # one vector per text, each exactly `dimensions` floats

pookiedb.embeddings(my_embed, dimensions=1536)

# or any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, vLLM, ...)
pookiedb.embeddings(
    pookiedb.openai_compatible("https://openrouter.ai/api/v1", api_key=KEY, model="openai/text-embedding-3-small"),
    dimensions=1536,
)
```

If the callback fails or returns the wrong shape, `pookiedb.EmbeddingError` is raised and nothing is written.
Vectors live in pgvector on PostgreSQL (the extension must be installed on the server; hosted
Postgres like Supabase or Neon includes it), and in the table on SQLite (searched with
[zvec](https://github.com/alibaba/zvec)).
See the [search docs](https://pookiedb.readthedocs.io/en/latest/#search) for modes, options and `pookiedb embed`.

---

## Field Reference

| Field                                      | Description                           |
| ------------------------------------------ | ------------------------------------- |
| `CharField(max_length=N)`                  | VARCHAR with length limit             |
| `TextField()`                              | Unlimited text                        |
| `IntegerField()`                           | INTEGER                               |
| `BigIntegerField()`                        | BIGINT                                |
| `FloatField()`                             | REAL / DOUBLE PRECISION               |
| `DecimalField(max_digits, decimal_places)` | NUMERIC                               |
| `BooleanField()`                           | BOOLEAN / INTEGER                     |
| `DateField(auto_now, auto_now_add)`        | DATE                                  |
| `DateTimeField(auto_now, auto_now_add)`    | TIMESTAMP                             |
| `TimeField()`                              | TIME                                  |
| `EmailField()`                             | VARCHAR with email validation         |
| `URLField()`                               | VARCHAR with URL validation           |
| `SlugField()`                              | VARCHAR with slug validation          |
| `UUIDField(auto=True)`                     | UUID / TEXT                           |
| `AutoField()`                              | Auto-increment primary key            |
| `BigAutoField()`                           | Big auto-increment primary key        |
| `AutoUUIDField()`                          | UUIDv7 primary key (auto-generated)   |
| `JSONField()`                              | JSONB (Postgres) / TEXT (SQLite)      |
| `ArrayField(base_field)`                   | ARRAY (Postgres) / JSON TEXT (SQLite) |
| `ForeignKey(to, on_delete)`                | Many-to-one FK                        |
| `OneToOneField(to, on_delete)`             | Unique FK                             |
| `ManyToManyField(to)`                      | Join table relationship               |

### Primary keys

Primary keys are always generated for you and can never be set by hand.

```python
class Student(pookiedb.Model):
    userTag = pookiedb.AutoUUIDField()  # UUIDv7 primary key; no `id` column is added
    name = pookiedb.TextField()
```

- No primary key declared → `id = AutoField()` is added.
- Only `AutoField`, `BigAutoField` and `AutoUUIDField` can be primary keys, one per model.
- `id` is reserved for the primary key, and `pk` is reserved as its alias (`get(pk=...)` works whatever the key is named).
- Passing or assigning a primary key (`Student(userTag=...)`, `obj.userTag = ...`, `bulk_update(userTag=...)`) raises `FieldError`.

### Common field kwargs

```python
CharField(
    max_length=100,
    null=False,         # allow NULL in DB
    blank=False,        # allow empty value in forms/validation
    default=None,       # default value (or callable)
    unique=False,       # UNIQUE constraint
    db_index=False,     # CREATE INDEX
    db_column=None,     # override column name
    choices=[("draft", "Draft"), ("pub", "Published")],
    verbose_name="My Field",
)
```

---

## Lookup Types

```python
# Exact (default)
filter(name="Alice")
filter(name__exact="Alice")

# Case-insensitive
filter(name__iexact="alice")
filter(name__icontains="ali")

# Wildcards
filter(title__startswith="Hello")
filter(title__endswith="world")
filter(body__contains="pookie")

# Comparison
filter(age__gt=18)
filter(age__gte=18)
filter(age__lt=65)
filter(age__lte=65)

# IN / NULL / RANGE
filter(id__in=[1, 2, 3])
filter(email__isnull=True)
filter(age__range=(18, 65))
filter(name__ne="Bob")
```

---

## Migrations

### Generate a migration

```bash
pookiedb makemigrations --settings settings.py
pookiedb makemigrations --name add_slug_field --settings settings.py
```

### Apply migrations

```bash
pookiedb migrate --settings settings.py
```

### View migration status

```bash
pookiedb showmigrations --settings settings.py
```

### Roll back

```bash
pookiedb rollback --steps 1 --settings settings.py
```

Your `settings.py` just needs to call `pookiedb.connect()` and import your models:

```python
# settings.py
import pookiedb
from myapp.models import Author, Post, Tag

pookiedb.connect("sqlite:///mydb.sqlite3")
```

---

## CLI Commands

```
pookiedb --help

Commands:
  makemigrations   Detect model changes and generate a migration file
  migrate          Apply pending migrations to the database
  rollback         Roll back the last N migrations
  showmigrations   List all migrations and their status
  shell            Interactive Python REPL with pookiedb pre-imported
  dbshell          Open raw psql / sqlite3 shell
  inspectdb        Introspect an existing DB and generate model code
  embed            Embed rows of searchable models that have no current embedding
```

### Shell

```bash
pookiedb shell --settings settings.py
# All your models are available by name
>>> Author.objects.all()
>>> Post.objects.filter(published=True).count()
```

### DBShell

```bash
pookiedb dbshell --settings settings.py   # opens psql or sqlite3
```

### InspectDB

```bash
# Introspect all tables
pookiedb inspectdb --settings settings.py

# Single table
pookiedb inspectdb --table users --settings settings.py

# Write to file
pookiedb inspectdb --output models.py --settings settings.py
```

---

## Transactions

```python
from pookiedb.db.connection import transaction

with transaction() as conn:
    author = Author(name="Bob", email="bob@example.com")
    author.save()
    post = Post(title="Hello", slug="hello", body="...", author=author)
    post.save()
    # Auto commits on exit, rolls back on exception
```

---

## Pagination

```python
from pookiedb.utils import Paginator

paginator = Paginator(Post.objects.filter(published=True), per_page=20)
page = paginator.page(1)

print(page.object_list)     # list of Post instances
print(page.has_next())      # True / False
print(page.num_pages)       # total pages
```

---

## Multiple Databases

```python
pookiedb.connect("postgresql://...", alias="primary")
pookiedb.connect("sqlite:///analytics.sqlite3", alias="analytics")

class EventLog(pookiedb.Model):
    class Meta:
        db_alias = "analytics"
```

---

## Model `Meta` options

```python
class Meta:
    db_table = "custom_table_name"      # override table name
    ordering = ["-created_at", "name"]  # default ordering
    unique_together = [["first_name", "last_name"]]
    db_alias = "default"                # which DB connection to use
    abstract = True                     # don't create a table
    verbose_name = "blog post"
    verbose_name_plural = "blog posts"
```

---

## License

MIT © Grace Peter Mutiibwa
