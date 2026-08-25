from __future__ import annotations

import argparse
import json
from pathlib import Path

from servicediscoverybench.retrieval.bge_dense import BGEConfig, BGEDenseRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Run registered BGE_DENSE_V2@200")
    parser.add_argument("--input", type=Path, required=True, help="Private JSONL manifest with query and candidate documents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    retriever = BGEDenseRetriever(BGEConfig(), device=args.device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for line in args.input.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            candidates = row["candidate_documents"]
            ranking = retriever.retrieve(
                row["query"],
                [item["candidate_id"] for item in candidates],
                [item["document"] for item in candidates],
                top_k=BGEConfig().top_depth,
            )
            handle.write(json.dumps({"request_id": row["request_id"], "retriever": "BGE_DENSE_V2", "k": min(200, len(candidates)), "ranking": ranking}, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
