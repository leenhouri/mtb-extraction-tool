"""matching.py - Value-level normalization and equality for scoring.

How a single extracted value is canonicalized (``norm``), how a dotted field path is
reduced to its field type (``field_type``), and how two normalized values are judged
equal (``field_equal``, including drug short/long INN forms). This is the "do these two
values mean the same thing?" layer; list and patient alignment live in ``alignment.py``.
"""
import re
import unicodedata


def _canon_date(m):
    """Zero-pad and 4-digit a dashed date: 24-11-29 -> 2024-11-29, 2024-1-5 -> 2024-01-05."""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y                       # clinical data is 2000s
    return f"{y}-{int(mo):02d}-{int(d):02d}"


# Tokens that all mean "empty/absent". LLM1 emits "unknown", LLM2 emits "Not
# documented"; both, plus blanks, must reconcile to None so empty==empty is a match.
_EMPTY = ("unknown", "not documented", "nicht dokumentiert", "unbekannt", "n/a", "na", "none")

# Equivalent wordings that should count as the SAME value. Keys and values are in
# post-norm() form (lowercased, punctuation stripped). Extend as needed.
_SYNONYMS = {
    # treatment type
    "systemic therapy": "systemic treatment",
    "systematic therapy": "systemic treatment",
    "systematic treatment": "systemic treatment",
    "maintenance therapy": "maintenance treatment",
    "systemic chemotherapy": "systemic treatment",
    # biomarker names (abbreviation / German / English -> one canonical form)
    "fralpha": "folate receptor alpha",
    "fr alpha": "folate receptor alpha",
    "folatrezeptor alpha": "folate receptor alpha",
    "folatrezeptoralpha": "folate receptor alpha",
    "folatreceptor alpha": "folate receptor alpha",
    "folatereceptoralpha": "folate receptor alpha",
    "folr1": "folate receptor alpha",
    # drug brand / abbreviation -> canonical generic (post-norm form: lowercase, no hyphens)
    "taxol": "paclitaxel",
    "caelyx": "pld",
    "pegylated liposomal doxorubicin": "pld",
    "pegyliertes liposomales doxorubicin": "pld",
    "liposomal doxorubicin": "pld",
    "liposomales doxorubicin": "pld",
    "carbo": "carboplatin",
    "gemzar": "gemcitabine",
    "avastin": "bevacizumab",
    "lynparza": "olaparib",
    "zejula": "niraparib",
    "elahere": "mirvetuximab",
    "mirvetuximab soravtansine": "mirvetuximab",
    "mirvetuximab soravtansin": "mirvetuximab",
    "mirvetuximabsoravtansine": "mirvetuximab",
    "mirvetuximabsoravtansin": "mirvetuximab",
}


def norm(v):
    """Canonicalize a value for comparison: lowercase, collapse whitespace, strip
    punctuation (including hyphens), canonicalize dates/units/percentages, apply
    _SYNONYMS, and map empty-equivalents (unknown / not documented / ...) to None."""
    if v is None or v == "" or (isinstance(v, str) and v.strip().lower() in _EMPTY):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        v = str(v)
    if isinstance(v, str):
        s = unicodedata.normalize("NFC", v).strip().lower()
        # unit exponents: m² / m^2 / cm³ / cm^3 -> m2 / cm3 (same quantity, different glyphs)
        s = s.translate({0x00B2: "2", 0x00B3: "3", 0x00B9: "1"})  # ² ³ ¹ superscripts
        s = re.sub(r"\^(\d)", r"\1", s)                            # caret form m^2 -> m2
        # canonicalize dashed dates BEFORE hyphens are stripped (fixes 2-digit years)
        s = re.sub(r"\b(\d{2,4})-(\d{1,2})-(\d{1,2})\b", _canon_date, s)
        s = re.sub(r"[,;:!/\\-]", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"(?<=\d) (?=[a-zµ%])", "", s)   # join number+unit: "30 mg" -> "30mg"
        s = s.strip(".")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)%", s)     # percentage -> fraction: "60%" -> "0.6"
        if m:
            s = ("%f" % (float(m.group(1)) / 100)).rstrip("0").rstrip(".")
        if not s:
            return None
        return _SYNONYMS.get(s, s)
    return v


def _identity(value) -> str:
    """Normalized identity token for alignment/keying ('?' when not a usable string)."""
    v = norm(value)
    if not isinstance(v, str):
        return "?"
    v = re.sub(r"[.\[\]#@]", "", v).strip()
    return v or "?"


def field_type(path: str) -> str:
    """Reduce a dotted field path to its schema field type (indices/keys stripped),
    e.g. 'doc0.diagnosis0.tumor' -> 'diagnosis.tumor', 'doc0.type' -> 'document.type'."""
    cleaned = []
    for p in path.split("."):
        token = re.sub(r"\[.*?\]", "", p)
        token = re.sub(r"\d+$", "", token)
        cleaned.append(token)
    if len(cleaned) == 2 and cleaned[0] == "doc":
        return f"document.{cleaned[1]}"
    return ".".join(t for t in cleaned if t and t != "doc")


def _is_med_name_field(path: str) -> bool:
    return path.rsplit(".", 1)[-1].lower() == "medicationname"


def _drug_match(a, b) -> bool:
    """True if two drug names denote the same agent, allowing one to be the short
    form (INN) of a longer name, e.g. 'Mirvetuximab' == 'Mirvetuximab soravtansin'."""
    na, nb = _identity(a), _identity(b)
    if na == "?" or nb == "?":
        return False
    if na == nb:
        return True
    ta, tb = na.split(), nb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return bool(short) and long_[:len(short)] == short      # shared leading INN token(s)


def field_equal(path, g, p) -> bool:
    """Equality used for scoring.
    - dates are compared DAY-EXACT (the full YYYY-MM-DD must agree). List alignment
      still pairs items by month proximity, so a day-level disagreement surfaces as a
      mismatch on the paired row rather than a phantom missing + extra;
    - medication names match on the shared INN (short vs long form);
    - any field with 'A OR B' alternatives matches if a value appears on both sides;
    - everything else uses exact normalized equality."""
    if g == p:
        return True
    if g is None or p is None:
        return False
    if _is_med_name_field(path) and _drug_match(g, p):
        return True
    ga = {x.strip() for x in g.split(" or ") if x.strip()}
    pa = {x.strip() for x in p.split(" or ") if x.strip()}
    return bool(ga & pa)
