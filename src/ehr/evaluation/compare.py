"""
compare.py
==========
Comparison table: gold standard vs two extraction methods, field by field.

One .xlsx, one row per flattened schema field:
  patient_id | variable | ground_truth | output_1 | output_2 | evaluation

- output_1 / output_2 cells are highlighted RED when they disagree with the gold value.
- The `evaluation` column scores GROUND TRUTH vs OUTPUT_2 (LLM2) per field:
    match | mismatch | missing (gold has it, LLM2 empty) | extra (LLM2 has it, gold empty)
  Empty-vs-empty counts as `match` (agreement on absence).

CONSISTENCY: this reuses the scorer's norm(), patient-normalization, content-based
alignment (_tr_sim/_med_sim/_surg_sim) and match rule (field_equal: day-exact dates, INN
drug names, "A OR B" alternatives), so the red cells, the evaluation column, and
evaluate.py's metrics always agree -- a cell is never red while looking identical. The
styled workbook is written by compare_xlsx.write_xlsx.

Usage
  uv run ehr-compare --gold gold --output1 output_llm1 --output2 output_llm2
  uv run ehr-compare --gold gold --output2 output_llm2          # output_1 optional
  uv run ehr-compare --demo
"""

import argparse
import json
from pathlib import Path

# Reuse the EXACT evaluation logic so the table and the metrics never disagree.
from ehr.evaluation.matching import norm, field_equal
from ehr.evaluation.alignment import keyed_items, _tr_sim, _med_sim, _surg_sim, SCORE_TREATMENT_LINE
from ehr.evaluation.normalization import normalize_patient
from ehr.evaluation.compare_xlsx import write_xlsx, EMPTY_LABEL


# --- joint flattening: gold and outputs aligned by similarity (no double-keying) -----
def _stable(norm_rec):
    """Display + compare dicts for the fields whose keys already align across sources
    (patient, document scalars, diagnosis, biomarker by name, tumor board by index)."""
    disp, cmp = {}, {}

    def put(key, val):
        disp[key] = val
        cmp[key] = norm(val)

    put("patient.id", norm_rec.get("id"))
    put("patient.dateOfBirth", norm_rec.get("dateOfBirth"))
    for d_i, doc in enumerate(norm_rec.get("documents", []) or []):
        for f in ["type", "date"]:
            put(f"doc{d_i}.{f}", doc.get(f))
        for dg_i, diag in enumerate(doc.get("diagnoses", []) or []):
            for f in ["tumor", "date", "figoStage", "tnmStage", "resectionStatus", "relapse"]:
                put(f"doc{d_i}.diagnosis{dg_i}.{f}", diag.get(f))
            for key, bm in keyed_items(diag.get("biomarkers"), "biomarker", ("type", "date")):
                base = f"doc{d_i}.diagnosis{dg_i}.biomarker[{key}]"
                for f in ["biomarker", "value", "type", "date"]:
                    put(f"{base}.{f}", bm.get(f))
        for tbo_i, tbo in enumerate(doc.get("tumorBoardOutcomes", []) or []):
            for f in ["date", "input", "recommendation"]:
                put(f"doc{d_i}.tumorBoard{tbo_i}.{f}", tbo.get(f))
    return disp, cmp


def _match_idx(gold_items, pred_items, sim, threshold=1):
    """Greedy best-match: returns {gold_idx: pred_idx} and the set of used pred indices."""
    cand = []
    for gi, g in enumerate(gold_items):
        for pi, p in enumerate(pred_items):
            s = sim(g, p)
            if s >= threshold:
                cand.append((s, gi, pi))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    g2p, used = {}, set()
    for _, gi, pi in cand:
        if gi in g2p or pi in used:
            continue
        g2p[gi] = pi
        used.add(pi)
    return g2p, used


def _slots(gold_items, o2_items, o1_items, has_out1, sim):
    """Build aligned slots, each {'gold':item|None,'out2':item|None,'out1':item|None},
    anchored on gold; unmatched output items become their own slots."""
    g2p2, used2 = _match_idx(gold_items, o2_items, sim)
    g2p1, used1 = _match_idx(gold_items, o1_items, sim) if has_out1 else ({}, set())
    slots = []
    for gi, g in enumerate(gold_items):
        slots.append({"gold": g,
                      "out2": o2_items[g2p2[gi]] if gi in g2p2 else None,
                      "out1": o1_items[g2p1[gi]] if (has_out1 and gi in g2p1) else None})
    left2 = [o2_items[i] for i in range(len(o2_items)) if i not in used2]
    left1 = [o1_items[i] for i in range(len(o1_items)) if has_out1 and i not in used1]
    if has_out1 and left2 and left1:
        l2, lused = _match_idx(left2, left1, sim)
        for i, o2 in enumerate(left2):
            slots.append({"gold": None, "out2": o2,
                          "out1": left1[l2[i]] if i in l2 else None})
        for j, o1 in enumerate(left1):
            if j not in lused:
                slots.append({"gold": None, "out2": None, "out1": o1})
    else:
        slots += [{"gold": None, "out2": o2, "out1": None} for o2 in left2]
        slots += [{"gold": None, "out2": None, "out1": o1} for o1 in left1]
    return slots


def _emit_slot(dicts, base, slot, fields):
    """Write a slot's fields into the (disp, cmp) dict pair of each present source."""
    for src in ("gold", "out2", "out1"):
        item = slot.get(src)
        if item is None or src not in dicts:
            continue
        disp, cmp = dicts[src]
        for f in fields:
            disp[f"{base}.{f}"] = item.get(f)
            cmp[f"{base}.{f}"] = norm(item.get(f))


def flatten_joint(gold_rec, o2_rec, o1_rec, has_out1):
    """Jointly flatten gold, LLM2 (and optionally LLM1) so that each treatment,
    medication, and surgery is aligned by similarity and shares one key across
    sources. Returns dict src -> (display_dict, compare_dict)."""
    g = normalize_patient(gold_rec or {})
    o2 = normalize_patient(o2_rec or {})
    o1 = normalize_patient(o1_rec or {}) if has_out1 else {}

    gd, gc = _stable(g)
    o2d, o2c = _stable(o2)
    dicts = {"gold": (gd, gc), "out2": (o2d, o2c)}
    if has_out1:
        o1d, o1c = _stable(o1)
        dicts["out1"] = (o1d, o1c)

    g_docs = g.get("documents", []) or []
    o2_docs = o2.get("documents", []) or []
    o1_docs = o1.get("documents", []) or []
    line_fields = ["type", "startDate", "endDate", "status"]
    if SCORE_TREATMENT_LINE:
        line_fields.append("treatmentLine")

    def trs(docs, d):
        return (docs[d].get("treatments", []) or []) if d < len(docs) else []

    def pool(docs, d, kind):
        if d >= len(docs):
            return []
        return [x for tr in (docs[d].get("treatments", []) or []) for x in (tr.get(kind) or [])]

    n_docs = max(len(g_docs), len(o2_docs), len(o1_docs))
    for d_i in range(n_docs):
        for slot_i, slot in enumerate(_slots(trs(g_docs, d_i), trs(o2_docs, d_i),
                                             trs(o1_docs, d_i), has_out1, _tr_sim)):
            _emit_slot(dicts, f"doc{d_i}.treatment[{slot_i}]", slot, line_fields)
        for slot_i, slot in enumerate(_slots(pool(g_docs, d_i, "medications"),
                                             pool(o2_docs, d_i, "medications"),
                                             pool(o1_docs, d_i, "medications"), has_out1, _med_sim)):
            _emit_slot(dicts, f"doc{d_i}.treatment.med[{slot_i}]", slot,
                       ["medicationName", "dosage", "interval", "startDate", "endDate"])
        for slot_i, slot in enumerate(_slots(pool(g_docs, d_i, "surgeries"),
                                             pool(o2_docs, d_i, "surgeries"),
                                             pool(o1_docs, d_i, "surgeries"), has_out1, _surg_sim)):
            _emit_slot(dicts, f"doc{d_i}.treatment.surgery[{slot_i}]", slot,
                       ["date", "type", "resectionStatus"])
    return dicts


def evaluate_pair(var, g_cmp, p_cmp):
    """GT vs prediction status for one field, mirroring evaluate.py's TP/TN/FN/FP.

    Uses field_equal (NOT plain ==) so dates agree day-exact, drug names agree on the
    shared INN form, and "A OR B" cells agree if either alternative matches -- exactly
    the rule that produces evaluate.py's P/R/F1."""
    if g_cmp is None and p_cmp is None:
        return "match"          # agreement on absence (TN)
    if g_cmp is not None and p_cmp is not None:
        return "match" if field_equal(var, g_cmp, p_cmp) else "mismatch"
    return "missing" if p_cmp is None else "extra"   # FN / FP


def show(disp_val, cmp_val):
    return EMPTY_LABEL if cmp_val is None else str(disp_val)


# --- loading ----------------------------------------------------------------
def load_source(path: str) -> dict:
    p = Path(path)
    if p.is_dir():
        return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(p.glob("*.json"))}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of patients")
    return {str(r.get("id", i)): r for i, r in enumerate(data)}


def build_rows(gold, out1, out2, has_out1):
    rows = []
    for pid, g in gold.items():
        dicts = flatten_joint(g, out2.get(pid, {}), out1.get(pid, {}), has_out1)
        g_disp, g_cmp = dicts["gold"]
        o2_disp, o2_cmp = dicts["out2"]
        o1_disp, o1_cmp = dicts.get("out1", ({}, {}))

        keys, seen = [], set()
        for k in list(g_cmp) + (list(o1_cmp) if has_out1 else []) + list(o2_cmp):
            if k not in seen:
                seen.add(k); keys.append(k)

        for var in keys:
            gc, o2c = g_cmp.get(var), o2_cmp.get(var)
            status = evaluate_pair(var, gc, o2c)
            row = {
                "pid": pid, "var": var,
                "gold": show(g_disp.get(var), gc),
                "out2": show(o2_disp.get(var), o2c),
                "o2_red": status != "match",          # consistent with field_equal
                "eval": status,
            }
            if has_out1:
                o1c = o1_cmp.get(var)
                row["out1"] = show(o1_disp.get(var), o1c)
                row["o1_red"] = not field_equal(var, gc, o1c)   # same rule as out2
            rows.append(row)
    return rows


# --- demo data (new schema) -------------------------------------------------
def demo_sources():
    def patient(pid, figo, tb_input, sys_start, surg_type="Längslaparotomie"):
        return {"id": pid, "dateOfBirth": "1958-03-12",
            "documents": [{"type": "Tumor Board Meeting Protocol", "date": "2019-05-20",
                "diagnoses": [{"tumor": "high-grade seröses Karzinom", "date": "2018-11-02",
                    "figoStage": figo, "tnmStage": "pT3c pN1 M0", "resectionStatus": "R0", "relapse": "No",
                    "biomarkers": [{"biomarker": "BRCA1", "value": "mutated", "type": "Germline", "date": "2018-11-15"}]}],
                "tumorBoardOutcomes": [{"date": "2019-05-20", "input": tb_input, "recommendation": "Chemotherapie"}],
                "treatments": [
                    {"type": "Surgery", "startDate": "2018-12-01", "endDate": None, "treatmentLine": 0, "status": "Completed",
                     "surgeries": [{"date": "2018-12-01", "type": surg_type, "resectionStatus": "R0"}]},
                    {"type": "Systemic Treatment", "startDate": sys_start, "endDate": "2019-04-15", "treatmentLine": 1, "status": "Completed",
                     "medications": [{"medicationName": "Carboplatin", "dosage": "AUC5", "interval": "q3w", "startDate": sys_start, "endDate": "2019-04-15"}]}]}]}
    gold = {"0001": patient("0001", "IIIC", "Erstdiagnose Therapieempfehlung", "2019-01-10"),
            "0002": patient("0002", "IVB",  "Rezidivtherapie",                 "2020-02-03")}
    # output_1 (LLM1): figo wrong on 0001, surgery type wrong on 0002
    out1 = {"0001": patient("0001", "IIIB", "Erstdiagnose Therapieempfehlung", "2019-01-10"),
            "0002": patient("0002", "IVB",  "Rezidivtherapie",                 "2020-02-03", surg_type="Laparoskopie")}
    # output_2 (LLM2): systemic startDate off on 0001 (still same month -> matches line), recommendation empty on 0002
    o2 = patient("0002", "IVB", "Rezidivtherapie", "2020-02-03")
    o2["documents"][0]["tumorBoardOutcomes"][0]["recommendation"] = "Not documented"
    out2 = {"0001": patient("0001", "IIIC", "Erstdiagnose Therapieempfehlung", "2019-01-15"), "0002": o2}
    return gold, out1, out2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold")
    ap.add_argument("--output1")
    ap.add_argument("--output2")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="comparison_table.xlsx")
    a = ap.parse_args()

    if a.demo:
        gold, out1, out2 = demo_sources()
        has_out1 = True
        a.out = a.out if a.out != "comparison_table.xlsx" else "comparison_demo.xlsx"
    else:
        gold = load_source(a.gold)
        out2 = load_source(a.output2)
        has_out1 = bool(a.output1)
        out1 = load_source(a.output1) if has_out1 else {}

    rows = build_rows(gold, out1, out2, has_out1)
    write_xlsx(rows, a.out, has_out1)
    print(f"{len(rows)} rows -> {a.out}")


if __name__ == "__main__":
    main()
