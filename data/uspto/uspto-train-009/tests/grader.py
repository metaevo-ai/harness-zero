"""Grader for text-classification/uspto-* instances: reactant-set exact match (order-free,
case-normalized) with Jaccard as auxiliary. Vendored from
metaevo-ai/mce-artifact (env/uspto), MIT.

Reads the agent's /app/answer.txt and the hidden /tests/answer.txt, writes
/logs/verifier/reward.txt (0 or 1) and metrics.json on every path.
"""

import json
import re
import pathlib

OUT = pathlib.Path("/logs/verifier")


def _extract_smiles(text: str) -> str:
    """Prefer the JSON {"final_answer": ...} shape; fall back to raw text."""
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            answer = data.get("final_answer")
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        except json.JSONDecodeError:
            pass
    return text.strip()


def _parse_reactants(smiles: str) -> set:
    return {part.strip().lower() for part in smiles.split(".") if part.strip()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ground_truth = pathlib.Path("/tests/answer.txt").read_text(encoding="utf-8").strip()
    answer_path = pathlib.Path("/app/answer.txt")
    raw = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else ""
    prediction = _extract_smiles(raw)
    pred_set = _parse_reactants(prediction)
    gt_set = _parse_reactants(ground_truth)
    exact = pred_set == gt_set
    if not pred_set and not gt_set:
        jaccard = 1.0
    elif not pred_set or not gt_set:
        jaccard = 0.0
    else:
        jaccard = len(pred_set & gt_set) / len(pred_set | gt_set)
    accuracy = 1.0 if exact else 0.0
    (OUT / "metrics.json").write_text(json.dumps({
        "accuracy": accuracy,
        "jaccard_similarity": jaccard,
        "prediction": prediction,
        "ground_truth": ground_truth,
        "answer_file_present": answer_path.is_file(),
    }, indent=2) + "\n")
    (OUT / "reward.txt").write_text(str(accuracy))
    print(f"grader: accuracy={accuracy} jaccard={jaccard:.2f} prediction={prediction!r}")


if __name__ == "__main__":
    main()
