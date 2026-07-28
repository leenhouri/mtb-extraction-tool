"""Deterministic normalization: dates + biomarker-name canonicalization
(ehr.evaluation.normalization)."""
from ehr.evaluation import normalization


def test_normalize_date_iso_german_and_partial():
    nd = normalization.normalize_date
    assert nd("2019-05-20") == "2019-05-20"      # ISO passthrough
    assert nd("20.05.2019") == "2019-05-20"      # German DD.MM.YYYY
    assert nd("01/18") == "2018-01-01"           # month/2-digit-year -> first of month
    assert nd("2020") == "2020-01-01"            # year only -> Jan 1
    assert nd("unknown") == normalization.UNKNOWN


def test_normalize_patient_normalizes_all_nested_dates():
    patient = {"dateOfBirth": "20.05.1960", "documents": [{
        "date": "02.01.2019",
        "diagnoses": [{"date": "2018", "biomarkers": [{"date": "01.2019"}]}],
        "treatments": [{"startDate": "20.05.2019", "endDate": None,
                        "medications": [{"startDate": "2019"}],
                        "surgeries": [{"date": "20.05.2019"}]}],
        "tumorBoardOutcomes": [{"date": "2019-05-20"}],
    }]}
    out = normalization.normalize_patient(patient)
    doc = out["documents"][0]
    assert out["dateOfBirth"] == "1960-05-20"
    assert doc["date"] == "2019-01-02"
    assert doc["diagnoses"][0]["date"] == "2018-01-01"
    assert doc["diagnoses"][0]["biomarkers"][0]["date"] == "2019-01-01"
    assert doc["treatments"][0]["startDate"] == "2019-05-20"
    assert doc["treatments"][0]["endDate"] is None          # None is left untouched
    assert doc["treatments"][0]["medications"][0]["startDate"] == "2019-01-01"
    assert doc["treatments"][0]["surgeries"][0]["date"] == "2019-05-20"
    assert doc["tumorBoardOutcomes"][0]["date"] == "2019-05-20"


def test_normalize_patient_does_not_mutate_input():
    patient = {"dateOfBirth": "20.05.1960", "documents": []}
    normalization.normalize_patient(patient)
    assert patient["dateOfBirth"] == "20.05.1960"           # deep-copied, original intact


def test_normalize_biomarker_name_canonicalizes_families():
    nb = normalization.normalize_biomarker_name
    assert nb("HER2/neu") == "HER2"
    assert nb("Her2") == "HER2"
    assert nb("c-erbB-2") == "HER2"
    assert nb("BRCA-1") == "BRCA1"
    assert nb("BRCA 2") == "BRCA2"
    assert nb("PDL1") == "PD-L1"
    assert nb("CA125") == "CA-125"
    assert nb("FRalpha") == "Folate-Receptor-Alpha"
    assert nb("FOLR1") == "Folate-Receptor-Alpha"
    assert nb("MSI-H") == "MSI"


def test_normalize_biomarker_name_passthrough_and_unknown():
    nb = normalization.normalize_biomarker_name
    assert nb("p53") == "p53"          # not in the map -> unchanged
    assert nb("BRCA") == "BRCA"        # unspecified BRCA is intentionally NOT inferred to BRCA1/2
    assert nb("unknown") == "unknown"  # empty-equivalent passthrough
    assert nb(None) is None


def test_normalize_patient_canonicalizes_biomarker_names():
    patient = {"documents": [{"diagnoses": [{"biomarkers": [
        {"biomarker": "HER2/neu"}, {"biomarker": "BRCA-1"}]}]}]}
    bms = normalization.normalize_patient(patient)["documents"][0]["diagnoses"][0]["biomarkers"]
    assert [b["biomarker"] for b in bms] == ["HER2", "BRCA1"]


def test_biomarker_name_variants_align_and_match():
    """Regression guard: a gold/pred name variant of the SAME marker (HER2/neu vs HER2,
    which the comparison-level synonym map does NOT cover) must align to one key and
    score as a match -- not split into a phantom missing + extra."""
    from ehr.evaluation.alignment import flatten_pair
    from ehr.evaluation.matching import field_equal
    gold = {"documents": [{"diagnoses": [{"biomarkers": [
        {"biomarker": "HER2/neu", "value": "negative", "type": "IHC"}]}]}]}
    pred = {"documents": [{"diagnoses": [{"biomarkers": [
        {"biomarker": "HER2", "value": "negative", "type": "IHC"}]}]}]}
    g, p = flatten_pair(normalization.normalize_patient(gold), normalization.normalize_patient(pred))
    g_keys = {k for k in g if ".biomarker[" in k}
    assert g_keys == {k for k in p if ".biomarker[" in k}    # aligned to identical keys
    name_key = next(k for k in g_keys if k.endswith(".biomarker"))
    assert field_equal(name_key, g[name_key], p[name_key])   # and the names match
