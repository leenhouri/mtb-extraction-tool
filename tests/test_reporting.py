"""Reporting helpers: significance (verdict parsing, McNemar, FDR) and Cohen's kappa."""
from ehr.evaluation.significance import _correct, mcnemar_exact, bh_fdr
from ehr.evaluation.interrater import cohen_kappa


def test_correct_parses_verdict_cells():
    assert _correct("match") is True
    assert _correct("Match") is True                       # case-insensitive
    assert _correct("mismatch") is False
    assert _correct("") is None                            # blank -> row skipped
    assert _correct(None) is None


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 0) == 2 * 0.5 ** 5             # all discordant one direction
    assert 0.0 <= mcnemar_exact(3, 7) <= 1.0


def test_bh_fdr_monotone_and_bounded():
    q = bh_fdr([0.001, 0.5, 0.5])
    assert all(0.0 <= x <= 1.0 for x in q)
    assert q[0] <= q[1]                                     # smallest p -> smallest q
    assert abs(q[0] - 0.003) < 1e-9                         # 0.001 * 3 / 1


def test_cohen_kappa_perfect_agreement():
    po, k, n = cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")])
    assert (po, k, n) == (1.0, 1.0, 4)


def test_cohen_kappa_total_disagreement_is_negative():
    po, k, n = cohen_kappa([("a", "b"), ("b", "a")])
    assert po == 0.0 and k <= 0.0 and n == 2


def test_cohen_kappa_empty_is_nan():
    po, k, n = cohen_kappa([])
    assert n == 0 and k != k                                # NaN
