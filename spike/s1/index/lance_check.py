"""The fallback store: the same passages, vectors and questions through LanceDB.

    python lance_check.py <workdir>

Reads what `pipeline.py` wrote (passages.jsonl, vectors.npy, question_vectors.npy) so the
comparison is the store alone: same passages, same vectors, same relevance rule. Run it in its
own environment, so its install size is its own. Prints one JSON report.
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa
from lancedb.rerankers import RRFReranker

HERE = Path(__file__).parent
DEPTH = 50


def directory_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6, 1)


def main() -> int:
    workdir = Path(sys.argv[1])
    passages = [json.loads(line) for line in (workdir / "passages.jsonl").read_text(encoding="utf-8").splitlines()]
    vectors = np.load(workdir / "vectors.npy")
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    question_vectors = np.load(workdir / "question_vectors.npy")
    answerable = questions["answerable"]

    store = workdir / "lance"
    shutil.rmtree(store, ignore_errors=True)
    started = time.perf_counter()
    db = lancedb.connect(str(store))
    data = pa.table(
        {
            "id": [p["id"] for p in passages],
            "text": [p["text"] for p in passages],
            "vector": pa.FixedSizeListArray.from_arrays(pa.array(vectors.ravel()), vectors.shape[1]),
        }
    )
    table = db.create_table("passages", data=data)
    table.create_fts_index("text")
    build_s = time.perf_counter() - started

    def vector_search(question: str, vector: np.ndarray) -> list[int]:
        return [row["id"] for row in table.search(vector).distance_type("cosine").limit(DEPTH).to_list()]

    def keyword_search(question: str, vector: np.ndarray) -> list[int]:
        return [row["id"] for row in table.search(question, query_type="fts").limit(DEPTH).to_list()]

    def hybrid_search(question: str, vector: np.ndarray) -> list[int]:
        query = table.search(query_type="hybrid").vector(vector).text(question).rerank(RRFReranker())
        return [row["id"] for row in query.limit(DEPTH).to_list()]

    report: dict[str, Any] = {
        "versions": {n: importlib.metadata.version(n) for n in ("lancedb", "pyarrow")},
        "build_s": round(build_s, 2),
        "store_mb": directory_mb(store),
        "methods": {},
    }
    for name, method in (("keywords", keyword_search), ("vectors", vector_search), ("hybrid_rrf", hybrid_search)):
        ranks: dict[str, int | None] = {}
        timings = []
        for question, vector in zip(answerable, question_vectors, strict=False):
            relevant = {p["id"] for p in passages if all(s in p["judged"] for s in question["must_contain"])}
            started = time.perf_counter()
            ranking = method(question["question"], vector)
            timings.append(time.perf_counter() - started)
            ranks[question["id"]] = next((r for r, pid in enumerate(ranking, 1) if pid in relevant), None)
        found = list(ranks.values())
        report["methods"][name] = {
            "hit@1": sum(r == 1 for r in found if r) / len(found),
            "hit@5": sum(r <= 5 for r in found if r) / len(found),
            "mrr@10": round(sum(1 / r for r in found if r and r <= 10) / len(found), 3),
            "median_query_ms": round(statistics.median(timings) * 1000, 2),
            "first_relevant_rank": ranks,
        }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
