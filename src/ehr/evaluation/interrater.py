#!/usr/bin/env python3
"""
interrater.py
=============
Inter-rater agreement between the two clinicians (Clinician 1 vs Clinician 2) on the gold standard,
per field type. Reports percent agreement and, in value mode, Cohen's kappa
(chance-corrected). Results are printed to the terminal and written to an .xlsx;
field types group exactly like the headline metrics.

Two input modes:
  (A) VALUE mode (enables kappa): two columns with each rater's ACTUAL value, e.g.
      --rater1-col "Ground Truth (Clinician 1)" --rater2-col "Ground Truth (Clinician 2)".
  (B) VERDICT mode (percent agreement only): one match/mismatch column, e.g.
      --verdict-col "Evaluation".

Usage:
  python -m ehr.evaluation.interrater table.xlsx --rater1-col "Ground Truth (Clinician 1)" --rater2-col "Ground Truth (Clinician 2)"
  python -m ehr.evaluation.interrater table.xlsx --verdict-col "Evaluation"
"""
import argparse
import re
from collections import defaultdict, Counter

from ehr.evaluation._tableio import load_rows, find, field_type, print_table, write_xlsx

# Empty-equivalent tokens folded to "" for comparison. NOTE: "unclear" is intentionally
# NOT here — it is a real controlled-vocabulary value (distinct from "Not documented"), so it
# is scored as a value, consistent with matching.py and agreement_stats.py.
_EMPTY = {"", "not documented", "nicht dokumentiert", "unknown", "unbekannt",
          "n/a", "na", "none", "null", "-", "nan"}


def normval(v):
    """Light normalization for comparison: lowercase, trim, empty-equivalents -> ''."""
    if v is None:
        return ""
    s = str(v).strip()
    # strip a trailing 00:00:00 time that Excel sometimes adds to dates
    s = re.sub(r"(\d{4}-\d{2}-\d{2})[ T]00:00(?::00)?(?:\.0+)?$", r"\1", s)
    low = s.lower()
    return "" if low in _EMPTY else low


def cohen_kappa(pairs):
    """pairs: list of (a, b) category labels. Returns (percent_agreement, kappa, n)."""
    n = len(pairs)
    if n == 0:
        return 0.0, float("nan"), 0
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe >= 1.0:
        kappa = 1.0 if po >= 1.0 else float("nan")
    else:
        kappa = (po - pe) / (1 - pe)
    return po, kappa, n


def canon_verdict(x):
    """Map a verdict cell to 'match'/'mismatch', or None if unrecognized."""
    if x is None:
        return None
    s = str(x).strip().lower()
    for lab in ("mismatch", "match"):
        if lab in s:
            return lab
    return None


def _kappa_str(k):
    """Format kappa to 3 dp, or '' for NaN (single category / undefined)."""
    return "" if (k != k) else f"{k:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--var-col", default=None)
    ap.add_argument("--rater1-col", default=None, help="rater 1 actual value (e.g. Clinician 1)")
    ap.add_argument("--rater2-col", default=None, help="rater 2 actual value (e.g. Clinician 2)")
    ap.add_argument("--verdict-col", default=None, help="single match/mismatch column (no kappa)")
    ap.add_argument("--out", default="interrater.xlsx")
    args = ap.parse_args()

    header, rows = load_rows(args.table, args.sheet)
    var_col = args.var_col or find(header, "variable", "field_path", "field")
    if not var_col:
        raise SystemExit(f"No variable column found. Headers: {header}")

    r1 = r2 = vcol = ""
    value_mode = bool(args.rater1_col or args.rater2_col)
    if value_mode:
        r1 = args.rater1_col or find(header, "ground truth (clinician 1)", "clinician 1", "ground_truth", "ground truth") or ""
        r2 = args.rater2_col or find(header, "ground truth (clinician 2)", "clinician 2") or ""
        if not (r1 and r2):
            raise SystemExit(f"Need both rater columns. Headers: {header}")
        print(f"VALUE mode: rater1={r1!r}  rater2={r2!r}  (computes % agreement + kappa)")
    else:
        vcol = args.verdict_col or find(header, "evaluation", "verdict") or ""
        if not vcol:
            raise SystemExit(f"No verdict column found. Headers: {header}")
        print(f"VERDICT mode: column={vcol!r}  (percent agreement only, no kappa)")

    by_field = defaultdict(list)
    verdicts = defaultdict(lambda: {"match": 0, "mismatch": 0})
    for r in rows:
        raw = r.get(var_col)
        if raw is None or not str(raw).strip():
            continue
        var = str(raw)
        ft = field_type(var)
        if value_mode:
            by_field[ft].append((normval(r.get(r1)), normval(r.get(r2))))
        else:
            lab = canon_verdict(r.get(vcol))
            if lab:
                verdicts[ft][lab] += 1

    if value_mode:
        headers = ["field_type", "n", "percent_agreement", "cohen_kappa"]
        out_rows = []
        all_pairs = []
        for ft in sorted(by_field):
            po, k, n = cohen_kappa(by_field[ft])
            out_rows.append([ft, n, f"{po * 100:.1f}", _kappa_str(k)])
            all_pairs.extend(by_field[ft])
        po, k, n = cohen_kappa(all_pairs)
        out_rows.append(["OVERALL", n, f"{po * 100:.1f}", _kappa_str(k)])
    else:
        headers = ["field_type", "n", "percent_agreement"]
        out_rows = []
        tot = {"match": 0, "mismatch": 0}
        for ft in sorted(verdicts):
            m, mm = verdicts[ft]["match"], verdicts[ft]["mismatch"]
            tot["match"] += m
            tot["mismatch"] += mm
            n = m + mm
            out_rows.append([ft, n, f"{(m / n * 100) if n else 0.0:.1f}"])
        n = tot["match"] + tot["mismatch"]
        out_rows.append(["OVERALL", n, f"{(tot['match'] / n * 100) if n else 0.0:.1f}"])

    print_table("Inter-rater agreement (Clinician 1 vs Clinician 2) per field type:", headers, out_rows)
    write_xlsx(args.out, headers, out_rows, "interrater")
    ovr = out_rows[-1]
    print(f"\nWritten: {args.out}")
    tail = f"  kappa={ovr[3]}" if (value_mode and ovr[3]) else ""
    print(f"Overall: {ovr[2]}% agreement  (n={ovr[1]}){tail}")


if __name__ == "__main__":
    main()
