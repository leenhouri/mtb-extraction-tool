"""Pharmacy active-ingredient canonicalisation (ehr.evaluation.drugs)."""
from ehr.evaluation.drugs import canon_drug


def test_canon_drug_aliases_and_cleaning():
    assert canon_drug("Carbo") == "carboplatin"
    assert canon_drug("Gemcitabin") == "gemcitabine"                    # German spelling
    assert canon_drug("Doxorubicin peg lipo") == "pegylated liposomal doxorubicin"
    assert canon_drug("Paclitaxel-S470") == "paclitaxel"               # hyphen->space + study code
    assert canon_drug("Paclitaxel Albumin") == "paclitaxel"            # nab-paclitaxel == paclitaxel


def test_canon_drug_drops_supportive_medications():
    assert canon_drug("Ondansetron") == ""
    assert canon_drug("Dexamethason") == ""


def test_canon_drug_empty_equivalents():
    assert canon_drug("") == ""
    assert canon_drug("unknown") == ""
    assert canon_drug(None) == ""


def test_mirvetuximab_forms_fold_to_one_canonical():
    canonical = "mirvetuximab soravtansine"
    for name in ["Mirvetuximab", "Mirvetuximab sorav", "Mirvetuximab Soravtansin", "Elahere"]:
        assert canon_drug(name) == canonical


def test_oral_external_agents_always_excluded():
    # Orally/externally dispensed agents the IV pharmacy never fills are dropped on both sides.
    assert canon_drug("Olaparib") == ""                                # oral PARP inhibitor
    assert canon_drug("Zejula") == ""                                  # brand -> niraparib (oral PARP)
    assert canon_drug("Tamoxifen") == ""                               # oral endocrine therapy
