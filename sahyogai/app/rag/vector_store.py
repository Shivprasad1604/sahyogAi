"""ChromaDB wrapper — PROSE ONLY.

What lives here: what a benefit is, how to apply, what documents to carry.
What does NOT live here: age limits, income caps, state, occupation.

Those are numeric and categorical constraints, and an embedding cannot
enforce them. "Available to citizens aged 18 to 40" and "aged 60 and above"
are nearly identical as text — same words, same structure, same topic — so
similarity search can confidently return the wrong one. Eligibility is a
SQL predicate in scheme_rules. See app/agents/schemes.py.

The metadata filter (`where`) runs BEFORE similarity ranking, so a
Maharashtra-only scheme can never surface for a Bihar user, however well
the text matches. Filter first, then rank.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.rag.chunking import chunk_text

log = logging.getLogger(__name__)

COLLECTION = "scheme_docs"
_collection = None


def get_collection():
    """Returns the Chroma collection, or None if Chroma is unavailable.
    None is not an error — callers fall back to SQL-only answers."""
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
        _collection = client.get_or_create_collection(
            name=COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        return _collection
    except Exception as exc:
        log.info("ChromaDB unavailable (%s). Scheme prose retrieval is skipped.", exc)
        return None


def index_document(
    scheme_code: str,
    scheme_name: str,
    text: str,
    state: str | None = None,
    occupation: str | None = None,
) -> int:
    """Store the PROSE of one scheme. Metadata carries the filterable fields."""
    collection = get_collection()
    if collection is None:
        return 0

    chunks = chunk_text(text)
    if not chunks:
        return 0

    collection.upsert(
        ids=[f"{scheme_code}-{i}" for i in range(len(chunks))],
        documents=chunks,
        metadatas=[
            {
                "scheme_code": scheme_code,
                "scheme_name": scheme_name,
                # "any" is the sentinel for a national / all-occupation scheme,
                # so one $in filter covers both cases.
                "state": (state or "any").lower(),
                "occupation": (occupation or "any").lower(),
            }
            for _ in chunks
        ],
    )
    return len(chunks)


def search(
    question: str,
    state: str | None = None,
    occupation: str | None = None,
    scheme_codes: list[str] | None = None,
    n_results: int = 5,
) -> list[dict]:
    collection = get_collection()
    if collection is None:
        return []

    clauses: list[dict] = []
    if state:
        clauses.append({"state": {"$in": [state.lower(), "any"]}})
    if occupation:
        clauses.append({"occupation": {"$in": [occupation.lower(), "any"]}})
    if scheme_codes:
        # Restrict to the schemes SQL already said this user qualifies for.
        clauses.append({"scheme_code": {"$in": scheme_codes}})

    where = None
    if len(clauses) == 1:
        where = clauses[0]
    elif len(clauses) > 1:
        where = {"$and": clauses}

    try:
        result = collection.query(
            query_texts=[question], n_results=n_results, where=where
        )
    except Exception as exc:
        log.warning("Vector search failed: %s", exc)
        return []

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    return [
        {
            "text": doc,
            "scheme_code": meta.get("scheme_code"),
            "scheme_name": meta.get("scheme_name"),
            "similarity": round(1 - dist, 3) if dist is not None else None,
        }
        for doc, meta, dist in zip(documents, metadatas, distances)
    ]


def reset() -> None:
    global _collection
    try:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    _collection = None
