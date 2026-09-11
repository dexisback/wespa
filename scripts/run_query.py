#!/usr/bin/env python3
"""CLI query runner."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.hybrid_retriever import answer_question


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--mode", default="hybrid", choices=["vector", "graph", "hybrid"])
    args = ap.parse_args()

    result = answer_question(args.question, args.mode)
    print(f"mode={result.retrieval_mode} latency={result.latency_ms}ms confidence={result.confidence} ({result.confidence_label})")
    print(f"answer: {result.answer}")
    print(f"sources: {result.sources}")
    if result.facts:
        print("facts:")
        for f in result.facts[:8]:
            print(f"  - {f.subject_name} -[{f.relation}]-> {f.object_name} ({f.confidence}, {f.source_name}, active={f.active})")
    if result.passages:
        print("passages:")
        for p in result.passages[:3]:
            print(f"  - [{p.source}] {p.text[:120]}...")
    if result.graph_path and result.graph_path.nodes:
        print("graph path nodes:", [n.label for n in result.graph_path.nodes])
        for pth in result.graph_path.paths:
            print("  path:", " -> ".join(pth))


if __name__ == "__main__":
    main()
