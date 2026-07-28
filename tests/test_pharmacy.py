"""Pharmacy concordance: tolerances and gold-medication loading from a table
(ehr.evaluation.pharmacy)."""
from ehr.evaluation.pharmacy import load_from_comparison_table, month_index, concordance


def test_concordance_tolerances():
    assert concordance(0, 0) == "strong"
    assert concordance(0, 1) == "strong"      # within 1 month
    assert concordance(0, 2) == "accept"      # within 2 months
    assert concordance(0, 5) == "off"
    assert concordance(0, None) is None


def test_load_from_comparison_table_reconstructs_medications(tmp_path):
    csv_path = tmp_path / "cmp.csv"
    csv_path.write_text(
        "patient_id,variable,ground_truth (Clinician 2)\n"
        "0001,doc0.diagnosis0.tumor,serous carcinoma\n"            # ignored: not a medication field
        "0001,doc0.treatment.med[0].medicationName,Carboplatin\n"
        "0001,doc0.treatment.med[0].startDate,2019-01-10\n"
        "0001,doc0.treatment.med[0].endDate,2019-04-15\n"
        "0001,doc0.treatment.med[1].medicationName,Elahere\n"      # brand -> mirvetuximab soravtansine
        "0001,doc0.treatment.med[1].startDate,2022-01-15\n"
        "0001,doc0.treatment.med[1].endDate,Not documented\n",
        encoding="utf-8")

    gold, _ = load_from_comparison_table(str(csv_path), "ground_truth (Clinician 2)")
    assert set(gold) == {"1"}                                      # pid_int('0001') -> '1'
    meds = gold["1"]
    assert "carboplatin" in meds
    assert "mirvetuximab soravtansine" in meds                     # alias applied

    si, ei, _, _ = meds["carboplatin"]
    assert si == month_index("2019-01-10")
    assert ei == month_index("2019-04-15")
    assert meds["mirvetuximab soravtansine"][1] is None            # "Not documented" end -> None
