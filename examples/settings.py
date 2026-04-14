import pookiedb

# ── Choose your database ──────────────────────────────────────────────────────

# SQLite (local dev / testing)
pookiedb.connect("sqlite:///pookie_demo.sqlite3")

# PostgreSQL (production)
# pookie.connect("postgresql://postgres:password@localhost:5432/pookie_demo")


# ── Import your models (so they get registered) ───────────────────────────────

from examples.models import Author, Post, Tag  # noqa
