#!/usr/bin/env python3
"""Seed corpus utility: verifies the local trusted corpus and optionally pulls RSS."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RAW_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rss", nargs="*", default=[], help="RSS feed URLs to fetch into data/raw")
    args = ap.parse_args()

    corpus = RAW_DIR / "seed_corpus.json"
    if corpus.exists():
        data = json.loads(corpus.read_text())
        docs = data.get("documents", [])
        print(f"local seed corpus: {len(docs)} documents from {len({d['source'] for d in docs})} sources")
    else:
        print("no seed corpus found at data/raw/seed_corpus.json")

    if args.rss:
        from app.ingestion.rss_ingester import fetch_rss

        docs = fetch_rss(args.rss)
        out = RAW_DIR / f"rss_{int(__import__('time').time())}.json"
        out.write_text(json.dumps({"documents": [d.model_dump(mode="json") for d in docs]}, indent=2))
        print(f"fetched {len(docs)} articles -> {out}")


if __name__ == "__main__":
    main()
