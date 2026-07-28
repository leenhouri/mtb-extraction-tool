"""Content-based alignment: the invariant that dominated development
(ehr.evaluation.alignment)."""
from ehr.evaluation.alignment import treatment_orphans, flatten_pair
from ehr.evaluation.normalization import normalize_patient


def _patient(treatments):
    return {"id": "1", "dateOfBirth": "1961-07-04",
            "documents": [{"type": "Physician Letter", "date": "2019-02-01",
                           "diagnoses": [], "tumorBoardOutcomes": [], "treatments": treatments}]}


# Gold: surgery is line 0, systemic line 1. Pred: same two lines, but the systemic line
# is numbered 2 and the surgery line 1, and they appear in the opposite order.
_SURGERY = {"type": "Surgery", "startDate": "2018-12-01", "status": "Completed",
            "surgeries": [{"date": "2018-12-01", "type": "Längslaparotomie", "resectionStatus": "R1"}]}
_SYSTEMIC = {"type": "Systemic Treatment", "startDate": "2019-01-10", "endDate": "2019-04-20",
             "status": "Completed",
             "medications": [{"medicationName": "Carboplatin", "dosage": "AUC5", "interval": "q3w",
                              "startDate": "2019-01-10", "endDate": "2019-04-20"}]}

GOLD = _patient([{**_SURGERY, "treatmentLine": 0}, {**_SYSTEMIC, "treatmentLine": 1}])
PRED = _patient([{**_SYSTEMIC, "treatmentLine": 2}, {**_SURGERY, "treatmentLine": 1}])


def test_content_alignment_no_orphans_despite_line_renumbering_and_reorder():
    g, p = normalize_patient(GOLD), normalize_patient(PRED)
    assert list(treatment_orphans(g, p)) == []


def test_flatten_pair_shares_medication_keys_across_sources():
    g, p = normalize_patient(GOLD), normalize_patient(PRED)
    gf, pf = flatten_pair(g, p)
    med_keys = [k for k in gf if "treatment.med" in k and k.endswith(".medicationName")]
    assert med_keys, "expected at least one aligned medication key"
    for k in med_keys:
        assert k in pf, f"medication key {k} was not aligned into the prediction (line split)"
