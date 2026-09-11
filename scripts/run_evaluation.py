#!/usr/bin/env python3
"""Runs the full evaluation suite and writes data/eval/results.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation.evaluate import run_evaluation


def main():
    print("running evaluation (retrieval + grounded answers across all modes) ...")
    results = run_evaluation()
    print(json.dumps(results, indent=2))
    print(f"\nwritten to data/eval/results.json")


if __name__ == "__main__":
    main()
