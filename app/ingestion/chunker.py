from __future__ import annotations

from ..config import CHUNK_OVERLAP, CHUNK_SIZE


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    step = max(size - overlap, 1)
    for i in range(0, len(text), step):
        piece = text[i : i + size].strip()
        if len(piece) < 60 and i > 0:
            break
        chunks.append(piece)
        if i + size >= len(text):
            break
    return chunks


def chunk_id_for(document_id: str, index: int) -> str:
    return f"{document_id}_c{index}"
