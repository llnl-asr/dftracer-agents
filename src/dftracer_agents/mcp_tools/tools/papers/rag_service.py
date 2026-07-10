"""Retrieval (no synthesis) over the local paper/article library — semantic + lexical.

Embeds every chunk of every entry in ``.dftracer_agents/resources/`` (see
``local_library_service.py``) with a local ``sentence-transformers`` model and
ranks them by cosine similarity to a query, combined with the existing
bottleneck/system-config keyword-expansion scoring from ``academic_service``.
Retrieval only — this module never calls an LLM itself; it returns ranked
passages for the calling agent (already an LLM) to read and reason over.

Degrades gracefully when the optional ``sentence-transformers`` dependency
isn't installed (``pip install -e '.[embeddings]'``): semantic scores are all
0 and ranking falls back to the pre-existing lexical scoring alone, exactly
like ``CORE_API_KEY`` degrading to "skip this source" rather than failing.

Cache layout (inside ``.dftracer_agents/resources/``, so it shares the same
pip/git exclusion as everything else in that hidden cache directory)::

    .dftracer_agents/resources/
      embeddings.npz         float32 (N, dim) matrix, one row per chunk
      embeddings_meta.json    parallel list: {paper_id, chunk_index, text, content_hash}

A chunk row is kept only while ``content_hash`` still matches its entry's
current text — edited/re-saved entries get their stale chunks dropped and
recomputed on the next ``rag_search`` call.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .academic_service import (
    _BOTTLENECK_KEYWORD_EXPANSION,
    _SYSTEM_KEYWORD_EXPANSION,
    _expand_query_terms,
    _score_paper_relevance,
)
from .local_library_service import _resources_dirs, _text_sidecar_path

EMBEDDINGS_VECTORS_RELPATH = ".dftracer_agents/resources/embeddings.npz"
EMBEDDINGS_META_RELPATH = ".dftracer_agents/resources/embeddings_meta.json"

#: Small (~80MB), fast on CPU, good general-purpose semantic quality — the
#: standard default for local retrieval. Override via DFTRACER_EMBEDDING_MODEL
#: for a different tradeoff (e.g. a larger model, or an offline-cached path).
_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_CHUNK_WORDS = 200
_CHUNK_OVERLAP_WORDS = 40
#: Bound per-entry embedding cost — full papers can be 10k+ words; this is
#: plenty of context for a passage-level retrieval match.
_MAX_TEXT_CHARS = 40_000

# Module-level singleton: loaded at most once per process, never re-attempted
# after a failure (an ImportError won't fix itself mid-process).
_EMBEDDER = None
_EMBEDDER_LOAD_ATTEMPTED = False


def embeddings_available() -> bool:
    return _get_embedder() is not None


def _get_embedder():
    global _EMBEDDER, _EMBEDDER_LOAD_ATTEMPTED
    if _EMBEDDER_LOAD_ATTEMPTED:
        return _EMBEDDER
    _EMBEDDER_LOAD_ATTEMPTED = True
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    model_name = os.environ.get("DFTRACER_EMBEDDING_MODEL", _DEFAULT_MODEL)
    try:
        _EMBEDDER = SentenceTransformer(model_name)
    except Exception:
        _EMBEDDER = None
    return _EMBEDDER


def _embed(texts: List[str]):
    """Return an (N, dim) float32 numpy array of normalized embeddings, or None."""
    model = _get_embedder()
    if model is None or not texts:
        return None
    import numpy as np
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_words: int = _CHUNK_WORDS,
                overlap_words: int = _CHUNK_OVERLAP_WORDS) -> List[str]:
    """Split *text* into overlapping word-count chunks.

    Overlap keeps a passage that straddles a chunk boundary from losing its
    surrounding context in either half.
    """
    words = text.split()
    if not words:
        return []
    step = max(1, chunk_words - overlap_words)
    chunks = []
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + chunk_words])
        if chunk.strip():
            chunks.append(chunk)
        if start + chunk_words >= len(words):
            break
    return chunks


def _entry_full_text(entry: Dict[str, Any]) -> str:
    dirs = _resources_dirs()
    stored_path = dirs["root"] / entry["filename"]
    text_path = _text_sidecar_path(stored_path) if entry["type"] == "paper" else stored_path
    body = ""
    if text_path.exists():
        try:
            body = text_path.read_text(errors="ignore")
        except Exception:
            body = ""
    text = f"{entry.get('title', '')}\n{entry.get('abstract', '')}\n{body}".strip()
    return text[:_MAX_TEXT_CHARS]


def _entry_text_hash(entry: Dict[str, Any]) -> str:
    return hashlib.sha1(_entry_full_text(entry).encode("utf-8", "ignore")).hexdigest()


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------

def _load_embed_cache() -> Tuple[List[Dict[str, Any]], Optional[Any]]:
    import numpy as np
    dirs = _resources_dirs()
    meta_path = dirs["root"] / EMBEDDINGS_META_RELPATH
    vec_path = dirs["root"] / EMBEDDINGS_VECTORS_RELPATH
    if not meta_path.exists() or not vec_path.exists():
        return [], None
    try:
        meta = json.loads(meta_path.read_text())
        vectors = np.load(vec_path)["vectors"]
    except Exception:
        return [], None
    if len(meta) != len(vectors):
        return [], None
    return meta, vectors


def _save_embed_cache(meta: List[Dict[str, Any]], vectors) -> None:
    import numpy as np
    dirs = _resources_dirs()
    meta_path = dirs["root"] / EMBEDDINGS_META_RELPATH
    vec_path = dirs["root"] / EMBEDDINGS_VECTORS_RELPATH
    meta_path.write_text(json.dumps(meta, indent=2))
    if vectors is None or len(vectors) == 0:
        vec_path.unlink(missing_ok=True)
        return
    np.savez_compressed(vec_path, vectors=np.asarray(vectors, dtype="float32"))


def ensure_embeddings(entries: List[Dict[str, Any]]) -> bool:
    """Bring the embedding cache up to date with *entries*. Returns True if it changed.

    Drops chunk rows for entries that no longer exist or whose text changed
    (content_hash mismatch), then embeds any entry missing from the cache.
    No-op (returns False) when the embedding model isn't installed.
    """
    import numpy as np
    if _get_embedder() is None:
        return False

    meta, vectors = _load_embed_cache()
    entries_by_id = {e["id"]: e for e in entries}

    keep_mask = []
    for m in meta:
        entry = entries_by_id.get(m["paper_id"])
        keep_mask.append(entry is not None and _entry_text_hash(entry) == m["content_hash"])

    changed = not all(keep_mask) if meta else False
    kept_meta = [m for m, keep in zip(meta, keep_mask) if keep]
    if vectors is not None and len(vectors):
        kept_vectors = vectors[np.array(keep_mask, dtype=bool)]
    else:
        kept_vectors = None

    embedded_pids = {m["paper_id"] for m in kept_meta}
    to_embed = [e for e in entries if e["id"] not in embedded_pids]

    add_meta: List[Dict[str, Any]] = []
    add_texts: List[str] = []
    for entry in to_embed:
        text = _entry_full_text(entry)
        content_hash = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        for idx, chunk in enumerate(_chunk_text(text)):
            add_meta.append({"paper_id": entry["id"], "chunk_index": idx,
                              "text": chunk, "content_hash": content_hash})
            add_texts.append(chunk)

    if add_texts:
        new_vectors = _embed(add_texts)
        if new_vectors is not None:
            all_meta = kept_meta + add_meta
            all_vectors = (new_vectors if kept_vectors is None or len(kept_vectors) == 0
                            else np.vstack([kept_vectors, new_vectors]))
            _save_embed_cache(all_meta, all_vectors)
            return True

    if changed:
        _save_embed_cache(kept_meta, kept_vectors)
    return changed


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def rag_search(
    query: str,
    entries: List[Dict[str, Any]],
    bottleneck: str = "",
    system_config: str = "",
    top_k: int = 5,
) -> Dict[str, Any]:
    """Rank *entries* by relevance to *query* (+ optional bottleneck/system context).

    Combines two independent signals into one ranked list:

    * **Semantic** — cosine similarity between the query embedding and every
      chunk's embedding; a paper's semantic score is its single best-matching
      chunk. Zero (and reported as unavailable) when ``sentence-transformers``
      isn't installed.
    * **Lexical** — the existing ``academic_service`` bottleneck/system
      keyword-expansion scoring (title/abstract term hits + recency), so
      ranking still works — just less precisely — with no dependency at all.

    Returns a dict with ``embeddings_available``, ``count``, and ``results``
    (each entry annotated with ``semantic_score``, ``lexical_score``,
    ``combined_score``, and the best-matching passage as ``chunk``).
    """
    model_available = embeddings_available()
    if model_available:
        ensure_embeddings(entries)
    meta, vectors = _load_embed_cache()

    semantic_by_paper: Dict[str, Dict[str, Any]] = {}
    if model_available and vectors is not None and len(vectors) and meta:
        query_text = " ".join(t for t in (query, bottleneck, system_config) if t)
        qvec = _embed([query_text])
        if qvec is not None:
            import numpy as np
            sims = vectors @ qvec[0]
            for i, m in enumerate(meta):
                sim = float(sims[i])
                cur = semantic_by_paper.get(m["paper_id"])
                if cur is None or sim > cur["semantic_score"]:
                    semantic_by_paper[m["paper_id"]] = {
                        "semantic_score": sim, "chunk": m["text"], "chunk_index": m["chunk_index"],
                    }

    query_terms = _expand_query_terms(f"{query} {bottleneck}", _BOTTLENECK_KEYWORD_EXPANSION)
    boost_terms = (_expand_query_terms(system_config, _SYSTEM_KEYWORD_EXPANSION)
                   if system_config else [])

    scored = []
    for entry in entries:
        lex_score, matched = _score_paper_relevance(entry, query_terms, boost_terms)
        sem = semantic_by_paper.get(entry["id"], {
            "semantic_score": 0.0, "chunk": (entry.get("abstract") or "")[:240], "chunk_index": None,
        })
        # Semantic similarity is the primary signal when available (that's the
        # point of adding it) — lexical score is the fallback/tie-breaker so
        # ranking degrades gracefully rather than going to zero without a model.
        combined = sem["semantic_score"] * 5.0 + lex_score
        scored.append((combined, sem["semantic_score"], lex_score, matched, sem["chunk"], entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_k = max(1, min(50, top_k))
    results = []
    for combined, sem_score, lex_score, matched, chunk, entry in scored[:top_k]:
        if combined <= 0:
            continue
        results.append({
            "id": entry["id"],
            "type": entry.get("type"),
            "title": entry.get("title"),
            "authors": entry.get("authors"),
            "year": entry.get("year"),
            "source": entry.get("source"),
            "url": entry.get("url"),
            "combined_score": round(combined, 4),
            "semantic_score": round(sem_score, 4),
            "lexical_score": round(lex_score, 4),
            "matched_terms": matched,
            "chunk": chunk,
        })

    return {
        "embeddings_available": model_available,
        "count": len(results),
        "results": results,
        "library_size": len(entries),
    }
