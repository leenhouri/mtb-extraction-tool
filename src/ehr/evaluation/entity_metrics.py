"""entity_metrics.py - Entity-level precision / recall / F1 from the comparison table.

Field-level agreement (``evaluate.py``) presupposes that an item was aligned at all: it
scores the values *inside* paired entities and is silent about entities the model missed
entirely or introduced spuriously. This tool closes that gap. Rows of the comparison
table are grouped into their entity instance (the field path minus its leaf, e.g.
``doc0.treatment.med[0]``), and an entity counts as present on a side when at least one
of its fields carries a non-empty value under ``matching.norm``:

    TP  entity present on both sides        FP  prediction only (invented)
    FN  gold only (missed)

Entity types come from ``matching.field_type``, so they group exactly like the headline
metrics. Prints a table and writes an .xlsx.
"""
import argparse
from collections import defaultdict

from ehr.evaluation._tableio import load_rows, find, print_table, write_xlsx
from ehr.evaluation.matching import norm, field_type

# The list-valued entities, keyed by their field_type prefix (see matching.field_type).
# Scalar groups (patient, document, diagnosis, tumorBoard) are not list-valued and have
# no presence/absence question to answer, so they are out of scope here.
ENTITIES = {
    "treatment": "Treatment",
    "treatment.med": "Medication",
    "treatment.surgery": "Surgery",
    "diagnosis.biomarker": "Biomarker",
}
ORDER = ["Treatment", "Medication", "Surgery", "Biomarker"]


def entity_of(path: str):
    """(label, instance) for a field path, or None if it is not a list-valued entity.

    The instance is the path minus its leaf field, so every field of one medication
    shares a key; the label comes from field_type minus its leaf, so grouping matches
    the scorer's field types."""
    if "." not in path:
        return None
    label = ENTITIES.get(field_type(path).rsplit(".", 1)[0])
    return (label, path.rsplit(".", 1)[0]) if label else None


def tally(rows, var_col, gold_col, pred_col, patient_col=None):
    """Fold comparison rows into per-entity-type {tp, fp, fn} counts."""
    # (patient, label, instance) -> [present_in_gold, present_in_pred]
    presence = defaultdict(lambda: [False, False])
    for row in rows:
        path = row.get(var_col)
        if not path: continue
        hit = entity_of(str(path).strip())
        if not hit: continue
        label, instance = hit
        key = (row.get(patient_col) if patient_col else "", label, instance)
        if norm(row.get(gold_col)) is not None: presence[key][0] = True
        if norm(row.get(pred_col)) is not None: presence[key][1] = True

    counts = {label: {"tp": 0, "fp": 0, "fn": 0} for label in ORDER}
    for (_, label, _), (in_gold, in_pred) in presence.items():
        if in_gold and in_pred: counts[label]["tp"] += 1
        elif in_pred:           counts[label]["fp"] += 1
        elif in_gold:           counts[label]["fn"] += 1
    return counts


def prf(c):
    """Precision, recall, F1 from a {tp, fp, fn} counter."""
    p = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
    r = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("table", help="comparison table (.xlsx or .csv)")
    ap.add_argument("--sheet", default=None, help="worksheet name (default: first)")
    ap.add_argument("--var-col", default=None, help="column holding the field path")
    ap.add_argument("--gold-col", default=None, help="column holding the gold value")
    ap.add_argument("--pred-col", default=None, help="column holding the model value")
    ap.add_argument("--patient-col", default=None, help="column holding the patient id")
    ap.add_argument("--out", default="entity_metrics.xlsx", help="output .xlsx path")
    args = ap.parse_args()

    header, rows = load_rows(args.table, args.sheet)
    var_col = args.var_col or find(header, "variable", "field_path", "field")
    gold_col = args.gold_col or find(header, "ground_truth", "ground truth", "gold")
    pred_col = args.pred_col or find(header, "output_2", "output2", "output", "predicted")
    patient_col = args.patient_col or find(header, "patient_id", "patient")

    missing = [n for n, c in (("--var-col", var_col), ("--gold-col", gold_col),
                              ("--pred-col", pred_col)) if not c]
    if missing:
        raise SystemExit(f"Could not resolve {', '.join(missing)}. Columns present: {header}")

    counts = tally(rows, var_col, gold_col, pred_col, patient_col)

    headers = ["Entity", "TP", "FP", "FN", "Precision", "Recall", "F1"]
    out_rows, total = [], {"tp": 0, "fp": 0, "fn": 0}
    for label in ORDER:
        c = counts[label]
        for k in total: total[k] += c[k]
        p, r, f = prf(c)
        out_rows.append([label, c["tp"], c["fp"], c["fn"],
                         f"{p:.2f}", f"{r:.2f}", f"{f:.2f}"])
    p, r, f = prf(total)
    out_rows.append(["Overall", total["tp"], total["fp"], total["fn"],
                     f"{p:.2f}", f"{r:.2f}", f"{f:.2f}"])

    print_table(f"Entity-level agreement ({gold_col} vs {pred_col})", headers, out_rows)
    print(f"\nWritten: {write_xlsx(args.out, headers, out_rows, 'Entity metrics')}")


if __name__ == "__main__":
    main()
