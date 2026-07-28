"""evaluate.py - Score LLM extractions against the gold standard (CLI).

Matches predicted Patient JSONs to gold JSONs by filename stem, flattens both with the
content-based alignment in ``alignment.py``, scores every field with ``matching.field_equal``
(TP / FP / FN / TN per field type), and writes a per-field comparison plus per-field-type
metrics. Dates are scored day-exact; list-valued fields are aligned by clinical content.

Run with --debug-treatments to print, per patient, any treatment line that found no
partner (a residual alignment failure worth inspecting).

Outputs:
  - comparison_<tag>.csv : per-patient, per-field comparison (for review)
  - metrics_<tag>.csv    : accuracy, precision, recall, F1, support per field type

Usage:
    uv run ehr-evaluate --pred output --gold gold_standard --tag run1
    uv run ehr-evaluate --pred output --gold gold_standard --tag run1 --debug-treatments
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import cast

try:
    import json5
except ImportError:
    json5 = None

from ehr.evaluation.normalization import normalize_patient
from ehr.evaluation.matching import field_equal, field_type
from ehr.evaluation.alignment import flatten_pair, treatment_orphans, _line_brief


def _repair_json_text(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def load_json_lenient(path: Path):
    """Parse JSON tolerantly: strict, then trailing-comma repair, then json5 if available."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json_text(text))
    except json.JSONDecodeError:
        pass
    if json5 is not None:
        try:
            return json5.loads(text)
        except Exception:
            pass
    print(f"  PARSE ERROR: could not parse {path.name} (skipping)")
    return None


def classify(path, g_val, p_val) -> str:
    """Per-field label: TN (both empty), TP (equal under field_equal), FN (pred empty,
    gold present), FP (gold empty, pred present), or MISMATCH (both present, differ)."""
    if g_val is None and p_val is None:
        return "TN"
    if field_equal(path, g_val, p_val):
        return "TP"
    if p_val is None:
        return "FN"
    if g_val is None:
        return "FP"
    return "MISMATCH"


def _tally(counts, ftype, match):
    """Fold one field's label into per-field-type tp/fp/fn/tn (MISMATCH = one FP + one FN)."""
    if match == "TP":
        counts[ftype]["tp"] += 1
    elif match == "FN":
        counts[ftype]["fn"] += 1
    elif match == "FP":
        counts[ftype]["fp"] += 1
    elif match == "MISMATCH":
        counts[ftype]["fp"] += 1
        counts[ftype]["fn"] += 1
    elif match == "TN":
        counts[ftype]["tn"] += 1


def score_patient(pid, gold, pred, counts, rows):
    """Classify every field of one flattened gold/pred pair: update per-field-type
    counts and append a comparison row for each non-TN field (TN is tallied only)."""
    for path in sorted(set(pred) | set(gold)):
        g_val, p_val = gold.get(path), pred.get(path)
        match = classify(path, g_val, p_val)
        ftype = field_type(path)
        _tally(counts, ftype, match)
        if match != "TN":
            rows.append({"patient_id": pid, "field_path": path, "field_type": ftype,
                         "gold": "" if g_val is None else g_val,
                         "predicted": "" if p_val is None else p_val, "match": match})


def compute_metrics(counts) -> dict:
    """Per field type: accuracy, precision, recall, F1, and support (= tp + fn)."""
    metrics = {}
    for ftype, c in sorted(counts.items()):
        tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
        total = tp + fp + fn + tn
        accuracy  = (tp + tn) / total if total else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall    = tp / (tp + fn) if (tp + fn) else 0.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        metrics[ftype] = {"accuracy": round(accuracy, 3), "precision": round(precision, 3),
                          "recall": round(recall, 3), "f1": round(f1, 3), "support": tp + fn}
    return metrics


def write_comparison_csv(rows, tag) -> str:
    """Write the per-field comparison rows to comparison_<tag>.csv; return the path."""
    path = f"comparison_{tag}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["patient_id", "field_path", "field_type", "gold", "predicted", "match"])
        w.writeheader()
        w.writerows(rows)
    return path


def write_metrics_csv(metrics, tag):
    """Write the per-field-type metrics as metrics_<tag>.csv."""
    with open(f"metrics_{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["field_type", "accuracy", "precision", "recall", "f1", "support"])
        w.writeheader()
        for ftype, m in sorted(metrics.items()):
            w.writerow({"field_type": ftype, **m})


def _print_orphans(pid, g_norm, p_norm):
    """Print treatment lines that found no partner; return (#orphan_lines, had_any)."""
    n, had = 0, False
    for d_i, g_orph, p_orph in treatment_orphans(g_norm, p_norm):
        had = True
        n += len(g_orph) + len(p_orph)
        print(f"\n[ORPHAN] patient {pid}, doc {d_i}:")
        for g in g_orph:
            print(f"    gold-only line  : {_line_brief(g)}")
        for p in p_orph:
            print(f"    pred-only line  : {_line_brief(p)}")
    return n, had


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--tag",  required=True)
    ap.add_argument("--debug-treatments", action="store_true",
                    help="print treatment lines that found no partner (residual alignment failures)")
    args = ap.parse_args()

    pred_dir, gold_dir = Path(args.pred), Path(args.gold)
    rows = []
    counts = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    scored = skipped = orphan_patients = orphan_lines = 0

    for gold_file in sorted(gold_dir.glob("*.json")):
        if not gold_file.stem:
            continue
        pid = gold_file.stem
        pred_file = pred_dir / f"{pid}.json"
        if not pred_file.exists():
            print(f"  WARN: no prediction for patient {pid}, skipping")
            skipped += 1
            continue
        gold_raw = load_json_lenient(gold_file)
        pred_raw = load_json_lenient(pred_file)
        if gold_raw is None or pred_raw is None:
            print(f"  Skipping patient {pid} (unparseable JSON)")
            skipped += 1
            continue
        g_norm = normalize_patient(cast(dict, gold_raw))
        p_norm = normalize_patient(cast(dict, pred_raw))
        gold, pred = flatten_pair(g_norm, p_norm)
        scored += 1

        if args.debug_treatments:
            n_orph, had = _print_orphans(pid, g_norm, p_norm)
            orphan_lines += n_orph
            orphan_patients += 1 if had else 0

        score_patient(pid, gold, pred, counts, rows)

    csv_path = write_comparison_csv(rows, args.tag)
    print(f"Written: {csv_path} ({len(rows)} rows)")

    metrics = compute_metrics(counts)
    write_metrics_csv(metrics, args.tag)
    print(f"Written: metrics_{args.tag}.csv ({len(metrics)} field types)")

    print(f"\nScored {scored} patients, skipped {skipped}.")
    if args.debug_treatments:
        print(f"Treatment-line orphans: {orphan_lines} lines across {orphan_patients} patients "
              f"(0 is ideal; each orphan is a line scored as missing+extra instead of match/mismatch).")
    print(f"\nMetrics by field type ({args.tag}):")
    print(f"{'Field type':<40} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6} {'n':>6}")
    print("-" * 76)
    for ftype, m in sorted(metrics.items()):
        print(f"{ftype:<40} {m['accuracy']:>6.2f} {m['precision']:>6.2f} "
              f"{m['recall']:>6.2f} {m['f1']:>6.2f} {m['support']:>6}")


if __name__ == "__main__":
    main()
