"""Paragraph-aware chunking.

Why this matters HERE specifically: fixed-size splitting would cut an
eligibility clause in half —

    "available to citizens aged 18 to"  [chunk ends]

That is exactly the content that must stay intact. So chunks are packed
whole-paragraph-first, and only an over-long paragraph is hard-split.
"""

from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        # An over-long paragraph is the only case we hard-split.
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + chunk_size].strip())
                start += chunk_size - overlap
            continue

        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            chunks.append(current.strip())
            current = paragraph

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]
