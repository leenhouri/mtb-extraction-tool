"""Deterministic normalization of extracted Patient data (dict -> dict, side-effect free).

Scope:
  1. DATE normalization: partial / German-format dates -> ISO (YYYY-MM-DD), so a format
     difference (e.g. "20.05.2019" vs "2019-05-20") is never counted as disagreement.
  2. BIOMARKER NAME canonicalization: a small, explicit, in-code synonym map folds
     spelling variants and true synonyms of the same marker onto one canonical name
     (e.g. "FRalpha" / "Folatrezeptor alpha" / "FOLR1" -> "Folate-Receptor-Alpha";
     "HER2/neu" / "Her2" -> "HER2"; "BRCA-1" -> "BRCA1"). This is applied symmetrically
     to gold and prediction. It maps ONLY reusable name equivalences, never a
     specific-value -> specific-value mapping, so it cannot inflate agreement on the
     marker's RESULT. It exists because biomarkers are aligned by name: an unmatched
     name (HER2 vs HER2/neu) otherwise splits into a phantom missing + extra pair.

No other synonym mapping is applied. Term variation in free-text and other enum fields
is left untouched for the human reviewer. Emptiness ("unknown" / "Not documented" /
blank) is reconciled at comparison time by evaluate.norm(), not here.
"""

import copy
import re

UNKNOWN = "unknown"
_UNKNOWN_SET = {"", "unknown", "not documented", "nicht dokumentiert", "unbekannt",
                "n/a", "na", "none", "k.a", "keine angabe"}


def _key(s: str) -> str:
    """Lowercase, hyphens->spaces, collapse whitespace, strip surrounding punctuation."""
    s = re.sub(r"-+", " ", s.strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \t\n\r.,;:!/\\")


def _is_unknown(v) -> bool:
    return v is None or (isinstance(v, str) and _key(v) in _UNKNOWN_SET)


# --- biomarker name canonicalization ------------------------------------------------
# Edit this map to add/justify synonyms. Each canonical name lists its accepted
# variants; matching is case- and punctuation-insensitive (hyphen / slash / space /
# dot are ignored), so e.g. "BRCA-1", "BRCA 1", "brca1" all collapse to the canonical.
_BIOMARKER_SYNONYMS: dict[str, list[str]] = {
    "Folate-Receptor-Alpha": [
        "Folate-Receptor-Alpha", "Folate Receptor Alpha", "FRalpha", "FR alpha",
        "FOLR1", "Folat-R", "Folatrezeptor alpha", "Folat-Rezeptor Alpha",
        "Folat-Rezeptor-Alpha", "Folate-Rezeptor-Alpha", "Folatrezeptor-alpha",
    ],
    "HER2":  ["HER2", "HER-2", "HER 2", "HER2/neu", "HER2 neu", "Her2", "cerbB2", "c-erbB-2"],
    "BRCA1": ["BRCA1", "BRCA-1", "BRCA 1"],
    "BRCA2": ["BRCA2", "BRCA-2", "BRCA 2"],
    "PD-L1": ["PD-L1", "PDL1", "PD L1"],
    "CA-125": ["CA-125", "CA 125", "CA125"],
    "ER":    ["ER", "Östrogenrezeptor", "Oestrogenrezeptor", "Estrogen Receptor"],
    "PR":    ["PR", "Progesteronrezeptor", "Progesterone Receptor"],
    "dMMR":  ["dMMR", "MMR deficient", "deficient MMR"],
    "pMMR":  ["pMMR", "MMR proficient", "proficient MMR"],
    "MSI":   ["MSI", "MSI-H", "MSI high"],
    "MSS":   ["MSS", "MSI stable"],
    # NOTE: "BRCA" (unspecified) is intentionally NOT mapped to BRCA1/BRCA2 — inferring
    # the gene would be a specific-value guess, not a synonym.
}


def _bm_clean(s: str) -> str:
    """Loose key for biomarker names: lowercase, drop hyphen/slash/space/dot."""
    return re.sub(r"[-/ .]", "", s.strip().lower())


_BIOMARKER_CANON: dict[str, str] = {}
for _canonical, _variants in _BIOMARKER_SYNONYMS.items():
    for _v in _variants:
        _BIOMARKER_CANON[_bm_clean(_v)] = _canonical


def normalize_biomarker_name(v):
    """Canonicalize a biomarker name via the synonym map; unknown names pass through."""
    if _is_unknown(v) or not isinstance(v, str):
        return v
    return _BIOMARKER_CANON.get(_bm_clean(v), v.strip())


# --- date normalization -------------------------------------------------------------
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})$")   # 05.03.2021
_MONTH_YEAR = re.compile(r"^(\d{1,2})[/.\-](\d{2}|\d{4})$")      # 01/18, 01.2018
_YEAR_ONLY = re.compile(r"^(\d{4})$")


def normalize_date(v):
    """ISO YYYY-MM-DD. Partial dates -> first of month / Jan 1; unrecognized -> unchanged."""
    if _is_unknown(v):
        return UNKNOWN
    if not isinstance(v, str):
        return v
    s = v.strip()
    if _ISO.match(s):
        return s
    m = _DMY.match(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _MONTH_YEAR.match(s)
    if m:
        mo, y = int(m.group(1)), m.group(2)
        y = ("20" + y) if len(y) == 2 else y          # 2-digit year assumed 20xx
        return f"{int(y):04d}-{mo:02d}-01"
    m = _YEAR_ONLY.match(s)
    if m:
        return f"{m.group(1)}-01-01"
    return s


# --- full-patient walk --------------------------------------------------------------
def normalize_patient(patient: dict) -> dict:
    """Return a deep copy with ISO dates and canonical biomarker names.
    All other fields are passed through unchanged (no synonym mapping)."""
    p = copy.deepcopy(patient)
    p["dateOfBirth"] = normalize_date(p.get("dateOfBirth"))

    for doc in p.get("documents") or []:
        doc["date"] = normalize_date(doc.get("date"))

        for diag in doc.get("diagnoses") or []:
            diag["date"] = normalize_date(diag.get("date"))
            for bm in diag.get("biomarkers") or []:
                bm["biomarker"] = normalize_biomarker_name(bm.get("biomarker"))
                bm["date"] = normalize_date(bm.get("date"))

        for tr in doc.get("treatments") or []:
            tr["startDate"] = normalize_date(tr.get("startDate"))
            if tr.get("endDate") is not None:
                tr["endDate"] = normalize_date(tr.get("endDate"))
            for med in tr.get("medications") or []:
                med["startDate"] = normalize_date(med.get("startDate"))
                if med.get("endDate") is not None:
                    med["endDate"] = normalize_date(med.get("endDate"))
            for surg in tr.get("surgeries") or []:
                surg["date"] = normalize_date(surg.get("date"))

        for tbo in doc.get("tumorBoardOutcomes") or []:
            tbo["date"] = normalize_date(tbo.get("date"))

    return p