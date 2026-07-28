"""Value normalization and equality (ehr.evaluation.matching)."""
from ehr.evaluation.matching import norm, field_equal, field_type, _drug_match


def test_norm_empty_equivalents_to_none():
    for v in ["unknown", "Not documented", "nicht dokumentiert", "n/a", "", None]:
        assert norm(v) is None


def test_norm_canonicalizes_dates():
    assert norm("2024-1-5") == "20240105"       # zero-pad month/day
    assert norm("24-11-29") == "20241129"       # 2-digit year -> 20xx
    assert norm("2018-06-01") == "20180601"


def test_norm_units_and_percentages():
    assert norm("60%") == "0.6"                 # percentage -> fraction
    assert norm("30 mg") == "30mg"              # join number + unit
    assert norm("1.5 m²") == "1.5m2"       # superscript glyph -> ascii


def test_norm_applies_synonyms():
    assert norm("Taxol") == "paclitaxel"
    assert norm("Carbo") == "carboplatin"
    assert norm("Elahere") == "mirvetuximab"


def test_field_equal_dates_are_day_exact():
    assert field_equal("doc0.diagnosis0.date", norm("2024-11-29"), norm("2024-11-29")) is True
    assert field_equal("doc0.diagnosis0.date", norm("2024-11-29"), norm("2024-11-01")) is False


def test_field_equal_drug_inn_short_long():
    assert field_equal("doc0.treatment.med0.medicationName",
                       norm("Bevacizumab"), norm("Bevacizumab biosimilar")) is True


def test_field_equal_a_or_b_alternatives():
    assert field_equal("x.value", norm("positive OR equivocal"), norm("equivocal")) is True
    assert field_equal("x.value", norm("positive"), norm("negative")) is False


def test_field_equal_none_guard():
    assert field_equal("x", None, "something") is False


def test_drug_match_inn_prefix_and_deliberate_non_synonym():
    assert _drug_match("Mirvetuximab", "Mirvetuximab soravtansin") is True
    assert _drug_match("doxorubicin", "PLD") is False   # PLD != bare doxorubicin


def test_field_type_grouping():
    assert field_type("doc0.diagnosis0.tumor") == "diagnosis.tumor"
    assert field_type("doc0.type") == "document.type"
    assert field_type("doc0.treatment[0].startDate") == "treatment.startDate"
    assert field_type("doc0.treatment.med[0].medicationName") == "treatment.med.medicationName"
