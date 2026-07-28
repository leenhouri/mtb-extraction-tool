"""alignment.py - Content-based pairing and joint flattening for the scorer.

List-valued fields are paired by clinical CONTENT, not list position or a hash key, so
a reordered or differently-numbered list is not penalized:

  - Treatment LINES are aligned by similarity (``_tr_sim``): start/end month proximity
    and shared drug names are the RELIABLE identity signals; type, status and the
    (unreliably numbered) treatmentLine are weak signals. When a line has no date/drug
    evidence, ANY single agreeing weak signal (type OR line OR status) is enough to
    propose a pairing, so a line whose number disagrees or is missing on one side still
    finds its partner instead of splitting into a mirrored missing + extra block.
  - MEDICATIONS are aligned globally across all lines (drug name + start-month).
  - SURGERIES are aligned globally by their own date-month.
  - BIOMARKERS are keyed by name (``keyed_items``).

``flatten_pair`` flattens a gold/prediction pair JOINTLY so matched items share a key.
Field values are still compared at full precision in matching.py, so a genuine day-level
or drug-swap disagreement surfaces as a MISMATCH on the paired row. Run the scorer with
--debug-treatments (``treatment_orphans``) to inspect any line that found no partner.
"""
import re
from collections import defaultdict

from ehr.evaluation.matching import norm, _identity, _drug_match

# Score the treatmentLine integer as a field? Once matching is content/month-based,
# the integer mostly reports line-numbering agreement. Set False to drop it entirely.
SCORE_TREATMENT_LINE = True


def _month(v) -> str:
    """First 6 digits (YYYYMM) of a normalized date. '' if not date-like.
    norm() strips hyphens, so an ISO date arrives here as '20180601'."""
    if not isinstance(v, str):
        return ""
    digits = re.sub(r"\D", "", v)
    return digits[:6] if len(digits) >= 6 else ""


def _month_idx(yyyymm: str):
    """YYYYMM string -> integer month index, or None."""
    if not yyyymm or len(yyyymm) < 6:
        return None
    return int(yyyymm[:4]) * 12 + (int(yyyymm[4:6]) - 1)


def _month_close(a: str, b: str, tol: int = 1) -> int:
    """2 if same month, 1 if within tol months, 0 otherwise (or not comparable)."""
    ia, ib = _month_idx(a), _month_idx(b)
    if ia is None or ib is None:
        return 0
    d = abs(ia - ib)
    return 2 if d == 0 else (1 if d <= tol else 0)


def _align(gold_items, pred_items, sim, threshold=1):
    """Greedy best-match alignment of two item lists by a similarity function.
    Returns a list of (gold_item|None, pred_item|None) pairs: matched pairs share
    an entry, unmatched gold items pair with None (counted as FN downstream), and
    unmatched pred items pair with None on the gold side (counted as FP).

    This replaces hash-key alignment, which split one logical item into two keys
    whenever a single keying field (e.g. startDate) was present on one side only."""
    gold_items = list(gold_items or [])
    pred_items = list(pred_items or [])
    cand = []
    for gi, g in enumerate(gold_items):
        for pi, p in enumerate(pred_items):
            s = sim(g, p)
            if s >= threshold:
                cand.append((s, gi, pi))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))     # highest similarity first, deterministic
    g_to_p, p_used = {}, set()
    for _, gi, pi in cand:
        if gi in g_to_p or pi in p_used:
            continue
        g_to_p[gi] = pi
        p_used.add(pi)
    pairs = []
    for gi, g in enumerate(gold_items):
        pairs.append((g, pred_items[g_to_p[gi]] if gi in g_to_p else None))
    for pi, p in enumerate(pred_items):
        if pi not in p_used:
            pairs.append((None, p))
    return pairs


def _tr_sim(g, p) -> int:
    """Similarity of two treatment lines.

    RELIABLE identity = start/end month proximity and shared drug names. These
    dominate (the `core` term). type, status and treatmentLine are WEAK signals:
    helpful as tie-breakers, but never required.

    When there is no date/drug evidence at all (core == 0) we fall back to the weak
    signals and accept ANY single agreement (same type, OR same line number, OR same
    status) as enough to PROPOSE a pairing. The previous version required type AND
    line to agree, so a line whose number disagreed or was missing on one side never
    found its partner and was double-counted as a missing (gold orphan) plus an extra
    (pred orphan) -- a phantom FP+FN for what was actually a correct extraction.

    Greedy one-use-each matching in _align still prevents two genuinely distinct
    lines (e.g. different types) from collapsing into one."""
    sm = _month_close(_month(norm(g.get("startDate"))), _month(norm(p.get("startDate"))))
    em = _month_close(_month(norm(g.get("endDate"))), _month(norm(p.get("endDate"))))
    g_drugs = {_identity(m.get("medicationName")) for m in (g.get("medications") or [])} - {"?"}
    p_drugs = {_identity(m.get("medicationName")) for m in (p.get("medications") or [])} - {"?"}
    shared = len(g_drugs & p_drugs)

    gt, pt = norm(g.get("type")), norm(p.get("type"))
    type_match = bool(gt and pt and gt == pt)
    gl, pl = norm(g.get("treatmentLine")), norm(p.get("treatmentLine"))
    line_match = bool(gl and pl and gl == pl)
    gs, ps = norm(g.get("status")), norm(p.get("status"))
    status_match = bool(gs and ps and gs == ps)

    core = 2 * sm + 2 * em + 2 * shared          # reliable: dates + drugs (>= 2 if any hit)
    weak = (1 if type_match else 0) + (1 if line_match else 0) + (1 if status_match else 0)

    if core > 0:
        return core + weak                       # strong evidence + weak tie-breakers
    return weak                                  # no dates/drugs: any agreeing signal pairs


def _med_sim(g, p) -> int:
    """Medications match only if the same drug (short/long INN form counts as same);
    same start-month is a bonus."""
    if not _drug_match(g.get("medicationName"), p.get("medicationName")):
        return 0
    return 1 + _month_close(_month(norm(g.get("startDate"))), _month(norm(p.get("startDate"))))


def _surg_sim(g, p) -> int:
    """Surgeries match by date month and, secondarily, type."""
    score = _month_close(_month(norm(g.get("date"))), _month(norm(p.get("date"))))
    gt, pt = norm(g.get("type")), norm(p.get("type"))
    if gt and pt and gt == pt:
        score += 1
    return score


def _dedup_key(items, key_fn, order_fields):
    """Group items by key_fn(item); within a shared key, order by order_fields so
    gold and prediction assign the SAME '#1','#2' suffixes. Yields (key, item)."""
    groups = defaultdict(list)
    for it in items or []:
        groups[key_fn(it)].append(it)
    for base, group in groups.items():
        if len(group) > 1:
            group = sorted(group, key=lambda it: tuple(str(norm(it.get(f)) or "") for f in order_fields))
        for i, it in enumerate(group):
            yield (base if i == 0 else f"{base}#{i}"), it


def keyed_items(items, key_field, order_fields):
    """Biomarker-style alignment: key by one normalized field (unchanged behavior)."""
    yield from _dedup_key(items, lambda it: _identity(it.get(key_field)), order_fields)


def _flatten_stable(patient: dict) -> dict:
    """Fields whose alignment is already stable (patient, document, diagnosis,
    biomarker by name, tumor board by index). Treatments, medications, and
    surgeries are handled jointly in flatten_pair, not here."""
    out = {}
    out["patient.id"] = norm(patient.get("id"))
    out["patient.dateOfBirth"] = norm(patient.get("dateOfBirth"))

    for d_i, doc in enumerate(patient.get("documents", []) or []):
        for f in ["type", "date"]:
            out[f"doc{d_i}.{f}"] = norm(doc.get(f))

        for dg_i, diag in enumerate(doc.get("diagnoses", []) or []):
            for f in ["tumor", "date", "figoStage", "tnmStage", "resectionStatus", "relapse"]:
                out[f"doc{d_i}.diagnosis{dg_i}.{f}"] = norm(diag.get(f))
            for key, bm in keyed_items(diag.get("biomarkers"), "biomarker", ("type", "date")):
                base = f"doc{d_i}.diagnosis{dg_i}.biomarker[{key}]"
                for f in ["biomarker", "value", "type", "date"]:
                    out[f"{base}.{f}"] = norm(bm.get(f))

        for tbo_i, tbo in enumerate(doc.get("tumorBoardOutcomes", []) or []):
            for f in ["date", "input", "recommendation"]:
                out[f"doc{d_i}.tumorBoard{tbo_i}.{f}"] = norm(tbo.get(f))

    return out


def _emit_pairs(g_out, p_out, base_fmt, pairs, fields):
    """Write each aligned pair under a shared key so matched items are compared
    field by field, unmatched gold items become FN, unmatched pred items FP."""
    for i, (g, p) in enumerate(pairs):
        base = base_fmt.format(i=i)
        for f in fields:
            if g is not None:
                g_out[f"{base}.{f}"] = norm(g.get(f))
            if p is not None:
                p_out[f"{base}.{f}"] = norm(p.get(f))


def flatten_pair(gold: dict, pred: dict):
    """Flatten gold and prediction JOINTLY so that treatments, medications, and
    surgeries are aligned by similarity before scoring, rather than by a brittle
    hash key. Returns (gold_flat, pred_flat) with identical keys for matched items."""
    g_out = _flatten_stable(gold)
    p_out = _flatten_stable(pred)

    g_docs = gold.get("documents", []) or []
    p_docs = pred.get("documents", []) or []
    line_fields = ["type", "startDate", "endDate", "status"]
    if SCORE_TREATMENT_LINE:
        line_fields.append("treatmentLine")

    for d_i in range(max(len(g_docs), len(p_docs))):
        g_doc = g_docs[d_i] if d_i < len(g_docs) else {}
        p_doc = p_docs[d_i] if d_i < len(p_docs) else {}
        g_trs = g_doc.get("treatments", []) or []
        p_trs = p_doc.get("treatments", []) or []

        _emit_pairs(g_out, p_out, f"doc{d_i}.treatment[{{i}}]",
                    _align(g_trs, p_trs, _tr_sim), line_fields)

        g_meds = [m for tr in g_trs for m in (tr.get("medications") or [])]
        p_meds = [m for tr in p_trs for m in (tr.get("medications") or [])]
        _emit_pairs(g_out, p_out, f"doc{d_i}.treatment.med[{{i}}]",
                    _align(g_meds, p_meds, _med_sim),
                    ["medicationName", "dosage", "interval", "startDate", "endDate"])

        g_surg = [s for tr in g_trs for s in (tr.get("surgeries") or [])]
        p_surg = [s for tr in p_trs for s in (tr.get("surgeries") or [])]
        _emit_pairs(g_out, p_out, f"doc{d_i}.treatment.surgery[{{i}}]",
                    _align(g_surg, p_surg, _surg_sim),
                    ["date", "type", "resectionStatus"])

    return g_out, p_out


def treatment_orphans(gold: dict, pred: dict):
    """Diagnostic: yield (doc_index, gold_orphans, pred_orphans) for treatment lines
    that found no partner under _tr_sim. A non-empty result is a residual alignment
    failure (a line scored as a block of missing + extra rather than match/mismatch)."""
    g_docs = gold.get("documents", []) or []
    p_docs = pred.get("documents", []) or []
    for d_i in range(max(len(g_docs), len(p_docs))):
        g_trs = (g_docs[d_i] if d_i < len(g_docs) else {}).get("treatments", []) or []
        p_trs = (p_docs[d_i] if d_i < len(p_docs) else {}).get("treatments", []) or []
        pairs = _align(g_trs, p_trs, _tr_sim)
        g_orph = [g for g, p in pairs if g is not None and p is None]
        p_orph = [p for g, p in pairs if g is None and p is not None]
        if g_orph or p_orph:
            yield d_i, g_orph, p_orph


def _line_brief(tr) -> str:
    """One-line human summary of a treatment line for the debug output."""
    parts = []
    for f in ("type", "treatmentLine", "status", "startDate", "endDate"):
        v = tr.get(f)
        if v not in (None, ""):
            parts.append(f"{f}={v}")
    drugs = [m.get("medicationName") for m in (tr.get("medications") or []) if m.get("medicationName")]
    if drugs:
        parts.append("drugs=" + "/".join(str(d) for d in drugs))
    return ", ".join(parts) or "(empty line)"
