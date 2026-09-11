#!/usr/bin/env python3
"""One command to build the entire memory from the seed corpus."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RAW_DIR
from app.ingestion.pipeline import ingest_documents, load_documents_from_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(RAW_DIR / "seed_corpus.json"))
    ap.add_argument("--extra", nargs="*", default=[], help="additional JSON document files to ingest")
    args = ap.parse_args()

    docs = load_documents_from_file(args.file)
    for extra in args.extra:
        docs += load_documents_from_file(extra)
    print(f"ingesting {len(docs)} documents into graph + vector + provenance stores ...")
    summary = ingest_documents(docs)
    print(summary.message)
    print(
        f"seen={summary.documents_seen} added={summary.documents_added} "
        f"dup={summary.documents_skipped_duplicate} failed={summary.documents_failed} "
        f"entities=+{summary.entities_added} rels=+{summary.relationships_added} "
        f"superseded={summary.facts_superseded} corroborated={summary.facts_corroborated} "
        f"conflicts={summary.conflicts_flagged} chunks={summary.chunks_embedded}"
    )
    if summary.errors:
        for e in summary.errors:
            print(f"  error: {e}")


if __name__ == "__main__":
    main()
