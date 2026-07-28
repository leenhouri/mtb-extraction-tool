#!/usr/bin/env python3
"""
significance.py
===============
Per-field agreement of two pre-scored verdict columns -- GS (the primary/better one) and
Human (the baseline) -- with the statistical significance of the difference.

Each row of the comparison table already carries a "match"/"mismatch" verdict for GS and
for Human, so no value comparison is done here (that is what makes the McNemar test
straightforward):
      agreement = match / total
The per-field GS-vs-Human difference is tested with an exact McNemar test on the paired
correct/incorrect outcomes; p-values are FDR-corrected (Benjamini-Hochberg) into q, with
stars (* < .05, ** < .01, *** < .001). The Overall row reports the pooled McNemar.

In this study both columns are the SAME model output scored against two references: GS is the
model vs the audited gold standard (independently corrected field-by-field against the source
protocols) and Human is the model vs the pre-audit human consolidation. The McNemar test
therefore measures the EFFECT OF THE AUDIT -- its discordant pairs come only from the fields the
auditor changed (where the two references differ) -- so a significant GS > Human means the model
matched the source-verified value where the initial human consolidation had not. (The tool itself
is reference-agnostic: it just consumes two match/mismatch columns.)

Results are grouped Category / Field, printed to the terminal, and written to an .xlsx.

Usage:
  python -m ehr.evaluation.significance table.xlsx
  python -m ehr.evaluation.significance table.xlsx --gs-col GS --human-col Human
"""
import argparse
from collections import defaultdict
from math import comb

from ehr.evaluation.matching import field_type
from ehr.evaluation._tableio import load_rows, find, print_table, write_xlsx

# field_type -> (Category, Field label), in report order.
FIELD_LABELS = {
    "patient.id": ("Patient", "ID"),
    "patient.dateOfBirth": ("Patient", "Date of birth"),
    "document.type": ("Document", "Type"),
    "document.date": ("Document", "Date"),
    "diagnosis.tumor": ("Diagnosis", "Tumor (histology)"),
    "diagnosis.date": ("Diagnosis", "Date"),
    "diagnosis.figoStage": ("Diagnosis", "FIGO stage"),
    "diagnosis.tnmStage": ("Diagnosis", "TNM stage"),
    "diagnosis.resectionStatus": ("Diagnosis", "Resection status"),
    "diagnosis.relapse": ("Diagnosis", "Relapse"),
    "diagnosis.biomarker.biomarker": ("Biomarker", "Name"),
    "diagnosis.biomarker.type": ("Biomarker", "Type"),
    "diagnosis.biomarker.value": ("Biomarker", "Value"),
    "diagnosis.biomarker.date": ("Biomarker", "Date"),
    "treatment.type": ("Treatment", "Type"),
    "treatment.startDate": ("Treatment", "Start date"),
    "treatment.endDate": ("Treatment", "End date"),
    "treatment.status": ("Treatment", "Status"),
    "treatment.treatmentLine": ("Treatment", "Treatment line"),
    "treatment.med.medicationName": ("Medication", "Name"),
    "treatment.med.dosage": ("Medication", "Dosage"),
    "treatment.med.interval": ("Medication", "Interval"),
    "treatment.med.startDate": ("Medication", "Start date"),
    "treatment.med.endDate": ("Medication", "End date"),
    "treatment.surgery.date": ("Surgery", "Date"),
    "treatment.surgery.type": ("Surgery", "Type"),
    "treatment.surgery.resectionStatus": ("Surgery", "Resection status"),
    "tumorBoard.date": ("Tumor board", "Date"),
    "tumorBoard.input": ("Tumor board", "Input"),
    "tumorBoard.recommendation": ("Tumor board", "Recommendation"),
}

HEADERS = ["category", "field", "n", "gs_pct", "human_pct", "delta_pp", "q"]


def _correct(cell):
    """Parse a pre-scored verdict cell: True for 'match', False for 'mismatch', None for
    a blank/unrecognized cell (the row is then skipped). Case-insensitive."""
    s = str(cell if cell is not None else "").strip().lower()
    if s == "match":
        return True
    if s == "mismatch":
        return False
    return None


def mcnemar_exact(b, c):
    """Two-sided exact McNemar test (binomial, p=0.5) on discordant counts b, c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def bh_fdr(pvals):
    """Benjamini-Hochberg q-values for a list of p-values (same order)."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])   # ascending p
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):                        # largest rank -> smallest
        i = order[rank - 1]
        running = min(running, pvals[i] * m / rank)
        q[i] = min(running, 1.0)
    return q


def _stars(p):
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def _agree(match, mismatch):
    tot = match + mismatch
    return (match / tot) if tot else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--var-col", default=None)
    ap.add_argument("--gs-col", default=None, help='GS verdict column (default: "GS")')
    ap.add_argument("--human-col", default=None, help='Human verdict column (default: "Human")')
    ap.add_argument("--out", default="significance.xlsx")
    args = ap.parse_args()

    header, rows = load_rows(args.table, args.sheet)
    var_col = args.var_col or find(header, "variable", "field_path", "field")
    gs_col = args.gs_col or find(header, "gs", "gold standard", "gold_standard")
    human_col = args.human_col or find(header, "human", "baseline")
    if not all([var_col, gs_col, human_col]):
        raise SystemExit("Could not resolve columns.\n"
                         f"  variable : {var_col}\n  GS       : {gs_col}\n"
                         f"  Human    : {human_col}\n"
                         f"Headers: {header}\nUse --var-col / --gs-col / --human-col.")
    assert var_col and gs_col and human_col   # narrowed for the type checker
    print(f"variable : {var_col!r}")
    print(f"GS       : {gs_col!r}  (primary)")
    print(f"Human    : {human_col!r}  (baseline)")

    gs_c = defaultdict(lambda: {"match": 0, "mismatch": 0})
    hu_c = defaultdict(lambda: {"match": 0, "mismatch": 0})
    disc = defaultdict(lambda: {"b": 0, "c": 0})   # b: GS correct & Human wrong; c: reverse
    n_inst = defaultdict(int)
    skipped = 0

    for r in rows:
        var = str(r.get(var_col) if r.get(var_col) is not None else "").strip()
        if not var:
            continue
        gs = _correct(r.get(gs_col))
        hu = _correct(r.get(human_col))
        if gs is None or hu is None:        # blank/unrecognized verdict -> skip the row
            skipped += 1
            continue
        ft = field_type(var)
        n_inst[ft] += 1
        gs_c[ft]["match" if gs else "mismatch"] += 1
        hu_c[ft]["match" if hu else "mismatch"] += 1
        if gs and not hu:
            disc[ft]["b"] += 1
        elif hu and not gs:
            disc[ft]["c"] += 1

    fields = [ft for ft in FIELD_LABELS if ft in n_inst] + \
             [ft for ft in sorted(n_inst) if ft not in FIELD_LABELS]
    qvals = bh_fdr([mcnemar_exact(disc[ft]["b"], disc[ft]["c"]) for ft in fields])

    out_rows = []
    tg = {"match": 0, "mismatch": 0}
    th = {"match": 0, "mismatch": 0}
    tot_b = tot_c = tot_n = 0
    for ft, q in zip(fields, qvals):
        cat, lbl = FIELD_LABELS.get(ft, (ft.split(".")[0], ft))
        gm, gmm = gs_c[ft]["match"], gs_c[ft]["mismatch"]
        hm, hmm = hu_c[ft]["match"], hu_c[ft]["mismatch"]
        ag, ah = _agree(gm, gmm), _agree(hm, hmm)
        out_rows.append([cat, lbl, n_inst[ft], f"{ag * 100:.1f}", f"{ah * 100:.1f}",
                         f"{(ag - ah) * 100:+.1f}", f"{q:.3f}{_stars(q)}"])
        tg["match"] += gm; tg["mismatch"] += gmm
        th["match"] += hm; th["mismatch"] += hmm
        tot_b += disc[ft]["b"]; tot_c += disc[ft]["c"]; tot_n += n_inst[ft]

    ag, ah = _agree(tg["match"], tg["mismatch"]), _agree(th["match"], th["mismatch"])
    p_all = mcnemar_exact(tot_b, tot_c)
    out_rows.append(["Overall", "", tot_n, f"{ag * 100:.1f}", f"{ah * 100:.1f}",
                     f"{(ag - ah) * 100:+.1f}", f"{p_all:.3f}{_stars(p_all)}"])

    print_table("GS vs Human agreement per field; q = FDR-corrected McNemar:", HEADERS, out_rows)
    write_xlsx(args.out, HEADERS, out_rows, "significance")
    print(f"\nWritten: {args.out}")
    if skipped:
        print(f"(skipped {skipped} rows with a blank/unrecognized verdict)")
    print(f"Overall: GS {out_rows[-1][3]}%  vs  Human {out_rows[-1][4]}%  "
          f"(delta {out_rows[-1][5]} pp, McNemar p={p_all:.3g})")


if __name__ == "__main__":
    main()
