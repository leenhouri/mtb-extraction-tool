"""drugs.py - Active-ingredient canonicalisation for the pharmacy concordance check.

Folds spelling, hyphenation, and brand/formulation variants of an antineoplastic agent
onto one canonical name (``canon_drug``), drops supportive medications the data model does
not capture, and drops orally/externally dispensed agents the institutional (intravenous)
pharmacy never fills.
"""
import re

# Orally or externally dispensed antineoplastic agents that the institutional (intravenous)
# pharmacy does not fill, so they cannot appear in the dispensing table even when correctly
# present in the gold standard. They are excluded from BOTH sides so the validation compares
# like with like (agents the pharmacy is actually able to dispense).
_ORAL_EXTERNAL = {
    "olaparib", "niraparib", "rucaparib",                 # oral PARP inhibitors (maintenance)
    "tamoxifen", "anastrozole", "letrozole", "exemestane", "megestrol",  # oral endocrine therapy
}

# Supportive medications present in the dispensing table that the data model does NOT
# capture (it records antineoplastic therapy only). These are excluded from BOTH sides
# so they neither count as misses nor as matches.
_SUPPORTIVE = {
    "aprepitant", "fosaprepitant", "ondansetron", "granisetron", "palonosetron",
    "dexamethason", "dexamethasone",
    "lipegfilgrastim", "pegfilgrastim", "filgrastim",
    "denosumab", "zoledronsaeure", "zoledronic acid",
}

# Active-ingredient canonicalization. Keys are in CLEANED form (lowercased; hyphen, dot,
# slash and parenthesis replaced by space; whitespace collapsed), so spelling, hyphenation
# and brand/formulation variants of the same ingredient fold onto one canonical name.
_DRUG_ALIASES = {
    # German -> English spelling
    "gemcitabin": "gemcitabine",
    "etoposid": "etoposide",
    "ifosfamid": "ifosfamide",
    "cyclophosphamid": "cyclophosphamide",
    "anastrozol": "anastrozole",
    "megestat": "megestrol",
    "doxetacel": "docetaxel",            # spelling slip in the source
    # pegylated liposomal doxorubicin family
    "doxorubicin peg lipo": "pegylated liposomal doxorubicin",
    "pld": "pegylated liposomal doxorubicin",
    "caelyx": "pegylated liposomal doxorubicin",
    # antibody-drug conjugates: fold brand / short / truncated forms onto one canonical
    "mirvetuximab soravtansin": "mirvetuximab soravtansine",
    "mirvetuximab sorav import": "mirvetuximab soravtansine",
    "mirvetuximab sorav": "mirvetuximab soravtansine",   # truncated dispensing label
    "mirvetuximab": "mirvetuximab soravtansine",          # short form -> the ADC
    "elahere": "mirvetuximab soravtansine",               # brand name
    "t dxd": "trastuzumab deruxtecan",
    # oral / study suffixes that denote the same ingredient
    "treosulfan o": "treosulfan",
    "bevacizumab r": "bevacizumab",
    "goserelin monatl": "goserelin",
    "paclitaxel s470": "paclitaxel",
    "paclitaxel albumin": "paclitaxel",   # nab-paclitaxel counted as paclitaxel (per clinician)
    "pembrolizumab plac s470": "pembrolizumab",
    # brand -> generic (as in the extraction prompt)
    "taxol": "paclitaxel",
    "carbo": "carboplatin",
    "gemzar": "gemcitabine",
    "avastin": "bevacizumab",
    "lynparza": "olaparib",
    "zejula": "niraparib",
}

_EMPTY = {"", "unknown", "not documented", "nicht dokumentiert", "unbekannt", "n/a", "na", "none"}


def is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in _EMPTY)


def canon_drug(name) -> str:
    """Canonical active ingredient. Cleans punctuation (hyphen/dot/slash/parenthesis),
    drops supportive medications (returns ''), then maps spelling/brand variants.
    Orally/externally dispensed agents are also dropped (returns '')."""
    if is_empty(name):
        return ""
    s = re.sub(r"[-/.()]", " ", str(name).strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s in _SUPPORTIVE:
        return ""                       # excluded: not an antineoplastic agent
    c = _DRUG_ALIASES.get(s, s)
    if c in _ORAL_EXTERNAL:
        return ""                       # excluded: not dispensed by the institutional pharmacy
    return c
