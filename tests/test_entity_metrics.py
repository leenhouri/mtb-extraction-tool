"""Entity-level presence counting (ehr.evaluation.entity_metrics)."""
from ehr.evaluation.entity_metrics import entity_of, tally, prf

VAR, GOLD, PRED, PID = "variable", "ground_truth", "output_2", "patient_id"


def _row(pid, path, gold, pred):
    return {PID: pid, VAR: path, GOLD: gold, PRED: pred}


def test_entity_of_recognizes_the_list_valued_entities():
    assert entity_of("doc0.treatment[0].startDate") == ("Treatment", "doc0.treatment[0]")
    assert entity_of("doc0.treatment.med[1].dosage") == ("Medication", "doc0.treatment.med[1]")
    assert entity_of("doc0.treatment.surgery[0].date") == ("Surgery", "doc0.treatment.surgery[0]")
    assert entity_of("doc0.diagnosis0.biomarker[cea].value") == (
        "Biomarker", "doc0.diagnosis0.biomarker[cea]")


def test_entity_of_ignores_scalar_groups():
    for path in ["patient.id", "doc0.type", "doc0.diagnosis0.tumor",
                 "doc0.tumorBoard0.recommendation"]:
        assert entity_of(path) is None


def test_tally_counts_tp_fp_fn():
    rows = [
        # present on both sides -> TP
        _row("1", "doc0.treatment[0].type", "Surgery", "Surgery"),
        _row("1", "doc0.treatment[0].startDate", "2019-01-10", "2019-01-11"),
        # gold only -> FN (the model missed this line entirely)
        _row("1", "doc0.treatment[1].type", "Systemic Treatment", ""),
        # prediction only -> FP (invented)
        _row("1", "doc0.treatment.med[0].medicationName", "", "Carboplatin"),
    ]
    counts = tally(rows, VAR, GOLD, PRED, PID)
    assert counts["Treatment"] == {"tp": 1, "fp": 0, "fn": 1}
    assert counts["Medication"] == {"tp": 0, "fp": 1, "fn": 0}


def test_tally_treats_empty_equivalents_as_absent():
    """'unknown' / 'Not documented' are empty per matching.norm, so an entity whose
    every field is an empty-equivalent is present on neither side."""
    rows = [
        _row("1", "doc0.treatment.surgery[0].date", "unknown", "Not documented"),
        _row("1", "doc0.treatment.surgery[0].resectionStatus", "", "n/a"),
    ]
    counts = tally(rows, VAR, GOLD, PRED, PID)
    assert counts["Surgery"] == {"tp": 0, "fp": 0, "fn": 0}


def test_same_instance_key_in_different_patients_is_not_merged():
    rows = [
        _row("1", "doc0.treatment[0].type", "Surgery", "Surgery"),
        _row("2", "doc0.treatment[0].type", "Surgery", "Surgery"),
    ]
    assert tally(rows, VAR, GOLD, PRED, PID)["Treatment"]["tp"] == 2


def test_prf_edges():
    assert prf({"tp": 0, "fp": 0, "fn": 0}) == (0.0, 0.0, 0.0)
    assert prf({"tp": 1, "fp": 1, "fn": 1}) == (0.5, 0.5, 0.5)
    p, r, f = prf({"tp": 3, "fp": 0, "fn": 0})
    assert (p, r, f) == (1.0, 1.0, 1.0)
