#!/usr/bin/env python3
"""
agreement_stats.py
==================
Supplementary agreement statistics for the gold-standard evaluation, complementing
``significance.py`` (per-field McNemar) and ``interrater.py`` (percent agreement + κ).
From a single human-curated comparison table it derives three things the headline
tables do not:

  (A) Model-vs-gold agreement per field and overall, each with a 95% confidence
      interval from a CLUSTER BOOTSTRAP that resamples PATIENTS with replacement (so a
      patient's fields stay together, respecting the within-patient dependence).
  (B) Inter-rater agreement between the two clinician extractions as Cohen's κ AND the
      prevalence-robust Gwet's AC1. AC1 is reported alongside κ because κ is deflated
      when one category dominates a high-prevalence field (e.g. treatment status),
      where the model's agreement is better read against AC1.
  (C) A "trivial agreement" decomposition: the share of fields that are 'Not
      documented' on BOTH sides, and the overall agreement once those jointly-absent
      fields are removed (documented-only agreement).

The input is the same comparison sheet the other reporting tools read; columns are
resolved leniently and can be overridden on the command line. All point estimates
(agreement, κ, AC1, jointly-absent share, documented-only agreement) are deterministic;
only the bootstrap CI depends on ``--seed`` / ``--bootstrap-n``.

Usage:
  python -m ehr.evaluation.agreement_stats comparison.xlsx
  python -m ehr.evaluation.agreement_stats comparison.xlsx \
      --rater1-col "Ground Truth (Clinician 1)" --rater2-col "Ground Truth (Clinician 2)" \
      --model-col output_2 --gs-col Evaluation
"""
import argparse
import random
from collections import defaultdict

from ehr.evaluation._tableio import load_rows, find, field_type, print_table, write_xlsx

# Values treated as "field absent / Not documented" for the trivial-agreement test.
# The extractor emits the canonical English "Not documented"; blanks/NaN cover empty cells.
ABSENT_TOKENS = {"", "not documented", "nan", "none"}

HEADERS = ["field", "n", "gold_pct", "ci_low", "ci_high",
           "interrater_pct", "cohen_kappa", "gwet_ac1"]


def _low(v):
    """Lowercased, stripped string form of a cell (None -> '')."""
    return "" if v is None else str(v).strip().lower()


def cohen_kappa_and_ac1(a, b):
    """Two-rater percent agreement, Cohen's κ, and Gwet's AC1 over the observed value
    categories. Returns ``(percent_agreement, kappa, ac1)``; κ/AC1 are ``None`` when a
    single category is observed (chance correction undefined). κ chance-corrects with the
    product of marginal prevalences (so it collapses toward 0 when one category dominates);
    AC1 chance-corrects with ``Σ p(1-p)/(K-1)``, which is robust to that prevalence."""
    pairs = list(zip(a, b))
    n = len(pairs)
    if n == 0:
        return 0.0, None, None
    cats = sorted({x for x, _ in pairs} | {y for _, y in pairs})
    K = len(cats)
    po = sum(1 for x, y in pairs if x == y) / n
    if K < 2:
        return po, None, None
    pa = {c: sum(1 for x, _ in pairs if x == c) / n for c in cats}
    pb = {c: sum(1 for _, y in pairs if y == c) / n for c in cats}
    pe_cohen = sum(pa[c] * pb[c] for c in cats)
    kappa = (po - pe_cohen) / (1 - pe_cohen) if pe_cohen < 1 else None
    pi = {c: (pa[c] + pb[c]) / 2 for c in cats}
    pe_gwet = sum(p * (1 - p) for p in pi.values()) / (K - 1)
    ac1 = (po - pe_gwet) / (1 - pe_gwet) if pe_gwet < 1 else None
    return po, kappa, ac1


def _percentile(sorted_vals, p):
    """Linear-interpolated percentile (numpy 'linear' / type-7 convention) of an
    already-sorted list. ``p`` in [0, 100]."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def cluster_bootstrap_ci(patient_gold, rng, n, level=0.95):
    """Percentile CI for overall agreement, resampling PATIENTS with replacement so a
    patient's fields stay together (cluster bootstrap). ``patient_gold`` maps
    ``patient -> list of 0/1`` gold outcomes; returns ``(lo, hi)`` in percent."""
    clusters = [(sum(v), len(v)) for v in patient_gold.values() if v]
    P = len(clusters)
    if P == 0:
        return float("nan"), float("nan")
    est = []
    for _ in range(n):
        s = c = 0
        for _ in range(P):
            cs, cc = clusters[rng.randrange(P)]
            s += cs
            c += cc
        est.append(s / c if c else 0.0)
    est.sort()
    lo = _percentile(est, 100 * (1 - level) / 2)
    hi = _percentile(est, 100 * (1 + level) / 2)
    return lo * 100, hi * 100


def _fmt(x, nd=3):
    return "" if x is None else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--var-col", default=None)
    ap.add_argument("--patient-col", default=None, help="patient id (cluster for the bootstrap)")
    ap.add_argument("--rater1-col", default=None, help="Clinician 1 value (inter-rater)")
    ap.add_argument("--rater2-col", default=None, help="Clinician 2 value (inter-rater / absent test)")
    ap.add_argument("--model-col", default=None, help="model output value (trivial-agreement test)")
    ap.add_argument("--gs-col", default=None, help="model-vs-gold verdict column (match/mismatch)")
    ap.add_argument("--bootstrap-n", type=int, default=20000, help="bootstrap resamples for the CIs")
    ap.add_argument("--seed", type=int, default=20260701, help="fixes the CIs for reproducibility")
    ap.add_argument("--out", default="agreement_stats.xlsx")
    args = ap.parse_args()

    header, rows = load_rows(args.table, args.sheet)
    var_col = args.var_col or find(header, "variable", "field_path", "field")
    pat_col = args.patient_col or find(header, "patient_id", "patient id", "patient")
    r1 = args.rater1_col or find(header, "ground truth (clinician 1)", "clinician 1",
                                 "rater 1", "ground_truth", "ground truth")
    r2 = args.rater2_col or find(header, "ground truth (clinician 2)", "clinician 2", "rater 2")
    model_col = args.model_col or find(header, "output_2", "output 2", "output_1", "model", "output")
    gs_col = args.gs_col or find(header, "gs", "evaluation", "human_evaluation", "verdict", "gold standard")

    missing = [name for name, col in [
        ("variable", var_col), ("patient", pat_col), ("rater1", r1),
        ("rater2", r2), ("model", model_col), ("GS verdict", gs_col)] if not col]
    if missing:
        raise SystemExit(f"Could not resolve columns: {', '.join(missing)}.\nHeaders: {header}\n"
                         "Override with --var-col / --patient-col / --rater1-col / --rater2-col / "
                         "--model-col / --gs-col.")
    print(f"variable={var_col!r} patient={pat_col!r} rater1={r1!r} rater2={r2!r} "
          f"model={model_col!r} GS={gs_col!r}")

    by_field = defaultdict(lambda: {"pg": defaultdict(list), "a": [], "b": []})
    all_pg = defaultdict(list)
    total = both_absent = informative_match = informative_total = 0

    for r in rows:
        raw = r.get(var_col)
        if raw is None or not str(raw).strip():
            continue
        ft = field_type(str(raw))
        patient = _low(r.get(pat_col)) or "?"
        gold = 1 if _low(r.get(gs_col)) == "match" else 0
        av, bv = _low(r.get(r1)), _low(r.get(r2))

        f = by_field[ft]
        f["pg"][patient].append(gold)
        f["a"].append(av)
        f["b"].append(bv)
        all_pg[patient].append(gold)
        total += 1
        if bv in ABSENT_TOKENS and _low(r.get(model_col)) in ABSENT_TOKENS:
            both_absent += 1
        else:
            informative_total += 1
            informative_match += gold

    if total == 0:
        raise SystemExit("No scored rows found in the table.")

    rng = random.Random(args.seed)
    out_rows = []
    for ft in sorted(by_field):
        f = by_field[ft]
        golds = [g for v in f["pg"].values() for g in v]
        gold_pct = 100 * sum(golds) / len(golds)
        lo, hi = cluster_bootstrap_ci(f["pg"], rng, args.bootstrap_n)
        po, kappa, ac1 = cohen_kappa_and_ac1(f["a"], f["b"])
        out_rows.append([ft, len(f["a"]), f"{gold_pct:.1f}", f"{lo:.1f}", f"{hi:.1f}",
                         f"{po * 100:.1f}", _fmt(kappa), _fmt(ac1)])

    all_golds = [g for v in all_pg.values() for g in v]
    lo, hi = cluster_bootstrap_ci(all_pg, rng, args.bootstrap_n)
    po, kappa, ac1 = cohen_kappa_and_ac1(
        [v for f in by_field.values() for v in f["a"]],
        [v for f in by_field.values() for v in f["b"]])
    out_rows.append(["OVERALL", total, f"{100 * sum(all_golds) / len(all_golds):.1f}",
                     f"{lo:.1f}", f"{hi:.1f}", f"{po * 100:.1f}", _fmt(kappa), _fmt(ac1)])

    both_pct = 100 * both_absent / total
    documented_only = 100 * informative_match / informative_total if informative_total else 0.0

    print_table("Agreement statistics per field "
                "(model-vs-gold with 95% cluster-bootstrap CI; inter-rater kappa & Gwet AC1):",
                HEADERS, out_rows)
    write_xlsx(args.out, HEADERS, out_rows, "agreement_stats")
    ovr = out_rows[-1]
    print(f"\nWritten: {args.out}")
    print(f"Overall model-vs-gold      : {ovr[2]}%  (95% CI {ovr[3]}-{ovr[4]}), n={ovr[1]}")
    print(f"Inter-rater                : {ovr[5]}%  Cohen kappa={ovr[6] or 'n/a'}  Gwet AC1={ovr[7] or 'n/a'}")
    print(f"Jointly 'Not documented'   : {both_absent} fields ({both_pct:.1f}% of {total})")
    print(f"Documented-only agreement  : {documented_only:.1f}%")


if __name__ == "__main__":
    main()
