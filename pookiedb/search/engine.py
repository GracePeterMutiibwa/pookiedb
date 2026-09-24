"""
Runs QuerySet.search(). Every mode returns model instances with
`search_score` (0–1, higher is better) and `search_snippet` attached.

- keyword:  PostgreSQL full-text search (ts_rank + ts_headline), falling back to a
            LIKE match when full-text finds nothing. SQLite always uses the LIKE match.
- semantic: cosine similarity against the stored embeddings.
            PostgreSQL uses pgvector; SQLite uses zvec when installed, else plain Python.
- hybrid:   keyword + semantic merged with reciprocal rank fusion.
"""

import math
import re

from pookiedb.exceptions import FieldError
from pookiedb.search.fields import VECTOR_FIELD, search_text
from pookiedb.search.sql import TS_CONFIG, text_sql, tsvector_sql

DEFAULT_LIMIT = 20      # results when the search isn't sliced
CANDIDATES = 50         # per-side candidates fetched before hybrid fusion
RRF_K = 60              # standard reciprocal-rank-fusion constant
LIKE_SCAN_LIMIT = 1000  # rows scored in Python by the LIKE fallback

HEADLINE_OPTIONS = "StartSel=**, StopSel=**, MinWords=15, MaxWords=30"


def run_search(qs) -> list:
    spec = qs._search
    meta = qs.model._meta
    fields = _search_fields(qs.model, spec["fields"])
    mode = spec["mode"] or ("hybrid" if meta.search_vector is not None else "keyword")
    if mode != "keyword" and meta.search_vector is None:
        raise FieldError(
            f"{mode} search needs embeddings: call pookie.embeddings() before "
            f"{qs.model.__name__} is defined."
        )

    limit = qs._limit if qs._limit is not None else DEFAULT_LIMIT
    wanted = qs._offset + limit
    query = spec["query"]

    if mode == "keyword":
        results = _keyword(qs, fields, query, wanted)
    elif mode == "semantic":
        results = _semantic(qs, fields, query, wanted, spec["min_similarity"])
    else:
        results = _hybrid(qs, fields, query, wanted, spec["min_similarity"])
    return results[qs._offset : wanted]


def _search_fields(model, names):
    searchable = model._meta.search_fields
    if not names:
        return searchable
    by_name = {f.name: f for f in searchable}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise FieldError(f"{model.__name__}: {missing} are not searchable fields.")
    return [by_name[n] for n in names]


def _annotate(obj, score: float, snippet: str):
    obj.__dict__["search_score"] = score
    obj.__dict__["search_snippet"] = snippet
    return obj


def _is_postgres(qs) -> bool:
    return qs._get_pool().config.is_postgres


def _where(qs) -> tuple[str, list]:
    where, params = qs._build_where()
    return (f" AND ({where})" if where else ""), params


# ── Keyword ──────────────────────────────────────────────────────────────────

def _keyword(qs, fields, query, n) -> list:
    if _is_postgres(qs):
        results = _keyword_postgres(qs, fields, query, n)
        if results:
            return results
    return _keyword_like(qs, fields, query, n)


def _keyword_postgres(qs, fields, query, n) -> list:
    table = qs.model._meta.db_table
    where, params = _where(qs)
    doc = tsvector_sql(fields)
    sql = (
        f"SELECT {qs._default_select()}, "
        f'ts_rank({doc}, q, 32) AS "_search_rank", '
        f"ts_headline('{TS_CONFIG}', {text_sql(fields)}, q, %s) AS \"_search_headline\" "
        f"FROM \"{table}\", websearch_to_tsquery('{TS_CONFIG}', %s) q "
        f"WHERE {doc} @@ q{where} "
        f'ORDER BY "_search_rank" DESC LIMIT {int(n)}'
    )
    rows = qs._execute(sql, [HEADLINE_OPTIONS, query] + params) or []
    results = []
    for row in rows:
        obj = qs.model._from_row(row)
        snippet = _with_ellipsis(row["_search_headline"], search_text(obj, fields))
        results.append(_annotate(obj, float(row["_search_rank"]), snippet))
    return results


def _keyword_like(qs, fields, query, n) -> list:
    terms = _terms(query)
    if not terms:
        return []
    table = qs.model._meta.db_table
    ph = qs._ph()
    where, params = _where(qs)
    matches, like_params = [], []
    for term in terms:
        for f in fields:
            matches.append(f'LOWER("{table}"."{f.get_column_name()}") LIKE {ph} ESCAPE \'\\\'')
            like_params.append(f"%{_escape_like(term)}%")
    sql = (
        f'SELECT {qs._default_select()} FROM "{table}" '
        f"WHERE ({' OR '.join(matches)}){where} LIMIT {LIKE_SCAN_LIMIT}"
    )
    rows = qs._execute(sql, like_params + params) or []

    scored = []
    for row in rows:
        obj = qs.model._from_row(row)
        text = search_text(obj, fields).lower()
        found = [t for t in terms if t in text]
        hits = sum(text.count(t) for t in found)
        scored.append((len(found) / len(terms), hits, obj))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [
        _annotate(obj, score, make_snippet(search_text(obj, fields), terms))
        for score, _, obj in scored[:n]
    ]


# ── Semantic ─────────────────────────────────────────────────────────────────

def _semantic(qs, fields, query, n, min_similarity) -> list:
    from pookiedb.search.embedder import embed_texts, get_config

    if min_similarity is None:
        min_similarity = get_config().min_similarity
    vector = embed_texts([query])[0]

    if _is_postgres(qs):
        ranked = _semantic_postgres(qs, vector, n)
    else:
        ranked = _semantic_sqlite(qs, vector, n)

    terms = _terms(query)
    return [
        _annotate(obj, score, make_snippet(search_text(obj, fields), terms))
        for obj, score in ranked
        if score >= min_similarity
    ]


def _semantic_postgres(qs, vector, n) -> list:
    meta = qs.model._meta
    table = meta.db_table
    cast = meta.search_vector.pg_type()
    literal = meta.search_vector.to_db(vector)
    where, params = _where(qs)
    distance = f'"{table}"."{VECTOR_FIELD}" <=> %s::{cast}'
    # ORDER BY the raw distance + LIMIT so pgvector's HNSW index is used
    sql = (
        f'SELECT {qs._default_select()}, {distance} AS "_search_distance" FROM "{table}" '
        f'WHERE "{table}"."{VECTOR_FIELD}" IS NOT NULL{where} '
        f"ORDER BY {distance} LIMIT {int(n)}"
    )
    rows = qs._execute(sql, [literal] + params + [literal]) or []
    return [(qs.model._from_row(r), 1.0 - float(r["_search_distance"])) for r in rows]


def _semantic_sqlite(qs, vector, n) -> list:
    from pookiedb.search import zvec_store

    ranked_ids = zvec_store.search(qs.model, vector, n)
    if ranked_ids is None:
        ranked_ids = _python_ranking(qs, vector)
    return _load_ranked(qs, ranked_ids, n)


def _python_ranking(qs, vector) -> list:
    """Exact cosine similarity over the stored embeddings, filters applied in SQL."""
    meta = qs.model._meta
    table = meta.db_table
    pk_col = meta.pk.get_column_name()
    where, params = _where(qs)
    sql = (
        f'SELECT "{table}"."{pk_col}" AS pk, "{table}"."{VECTOR_FIELD}" AS vec FROM "{table}" '
        f'WHERE "{table}"."{VECTOR_FIELD}" IS NOT NULL{where}'
    )
    rows = qs._execute(sql, params) or []
    q_norm = _norm(vector)
    ranked = []
    for row in rows:
        stored = meta.search_vector.to_python(row["vec"])
        ranked.append((row["pk"], _cosine(vector, q_norm, stored)))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def _load_ranked(qs, ranked_ids, n) -> list:
    """Fetch rows for (pk, score) pairs, applying the queryset's filters, keeping rank order."""
    if not ranked_ids:
        return []
    meta = qs.model._meta
    by_pk = {}
    # Chunked so large candidate lists stay under SQLite's parameter limit
    ids = [pk for pk, _ in ranked_ids]
    for i in range(0, len(ids), 500):
        chunk = qs.filter(pk__in=ids[i : i + 500])
        chunk._search = None
        chunk._limit, chunk._offset = None, 0
        for obj in chunk._fetch():
            by_pk[str(meta.pk.to_db(getattr(obj, meta.pk.name)))] = obj
    ranked = []
    for pk, score in ranked_ids:
        obj = by_pk.get(str(pk))
        if obj is not None:
            ranked.append((obj, score))
            if len(ranked) == n:
                break
    return ranked


# ── Hybrid ───────────────────────────────────────────────────────────────────

def _hybrid(qs, fields, query, n, min_similarity) -> list:
    pool = max(n, CANDIDATES)
    keyword = _keyword(qs, fields, query, pool)
    semantic = _semantic(qs, fields, query, pool, min_similarity)

    pk_name = qs.model._meta.pk.name
    fused, objects = {}, {}
    for results in (keyword, semantic):
        for rank, obj in enumerate(results, start=1):
            key = getattr(obj, pk_name)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            objects.setdefault(key, obj)  # keyword first, so its snippet wins

    best = 2.0 / (RRF_K + 1)  # ranked first in both lists
    ordered = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:n]
    return [_annotate(objects[k], score / best, objects[k].search_snippet) for k, score in ordered]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _terms(query: str) -> list[str]:
    seen = []
    for word in re.findall(r"\w+", query.lower()):
        if word not in seen:
            seen.append(word)
    return seen


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _cosine(q, q_norm, v) -> float:
    return sum(a * b for a, b in zip(q, v)) / (q_norm * _norm(v))


def make_snippet(text: str, terms: list[str], before: int = 15, after: int = 30) -> str:
    """~45 words around the first matching term, matches in **bold**, … where text was cut."""
    words = text.split()
    if not words:
        return ""
    start_at = 0
    for i, w in enumerate(words):
        if any(t in w.lower() for t in terms):
            start_at = i
            break
    start = max(0, start_at - before)
    end = min(len(words), start_at + after + 1)
    fragment = " ".join(words[start:end])
    if terms:
        pattern = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        fragment = re.sub(f"({pattern})", r"**\1**", fragment, flags=re.IGNORECASE)
    return ("…" if start > 0 else "") + fragment + ("…" if end < len(words) else "")


def _with_ellipsis(headline: str, text: str) -> str:
    plain = " ".join(headline.replace("**", "").split())
    full = " ".join(text.split())
    if not plain:
        return headline
    prefix = "" if full.startswith(plain) else "…"
    suffix = "" if full.endswith(plain) else "…"
    return prefix + headline.strip() + suffix
