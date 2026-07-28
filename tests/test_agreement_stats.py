"""Supplementary agreement statistics: Cohen's kappa / Gwet's AC1, percentile, and the
cluster bootstrap."""
import random

from ehr.evaluation.agreement_stats import (
    cohen_kappa_and_ac1,
    _percentile,
    cluster_bootstrap_ci,
)


def test_perfect_agreement_gives_unit_kappa_and_ac1():
    po, kappa, ac1 = cohen_kappa_and_ac1(["a", "b", "a", "b"], ["a", "b", "a", "b"])
    assert po == 1.0 and kappa == 1.0 and ac1 == 1.0


def test_single_category_leaves_chance_correction_undefined():
    po, kappa, ac1 = cohen_kappa_and_ac1(["x", "x", "x"], ["x", "x", "x"])
    assert po == 1.0 and kappa is None and ac1 is None


def test_total_disagreement_is_minus_one_for_both():
    po, kappa, ac1 = cohen_kappa_and_ac1(["x", "y"], ["y", "x"])
    assert po == 0.0 and kappa == -1.0 and ac1 == -1.0


def test_ac1_exceeds_kappa_under_high_prevalence():
    # 8/10 agree, but the "p" category dominates -> kappa is deflated, AC1 is not.
    a = ["p"] * 9 + ["n"]
    b = ["p"] * 8 + ["n", "p"]
    po, kappa, ac1 = cohen_kappa_and_ac1(a, b)
    assert po == 0.8
    assert kappa < 0.0 < ac1                 # kappa deflated below 0; AC1 stays high
    assert ac1 > 0.7


def test_percentile_linear_interpolation():
    vals = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert _percentile(vals, 0) == 0.0
    assert _percentile(vals, 100) == 4.0
    assert _percentile(vals, 50) == 2.0
    assert _percentile([], 50) != _percentile([], 50)   # NaN
    assert _percentile([7.0], 25) == 7.0


def test_cluster_bootstrap_all_ones_is_hundred_percent():
    pg = {"p1": [1, 1], "p2": [1], "p3": [1, 1, 1]}
    lo, hi = cluster_bootstrap_ci(pg, random.Random(1), n=200)
    assert lo == 100.0 and hi == 100.0


def test_cluster_bootstrap_is_seed_reproducible_and_bounded():
    pg = {"p1": [1, 0], "p2": [1, 1], "p3": [0, 0, 1], "p4": [1]}
    a = cluster_bootstrap_ci(pg, random.Random(20260701), n=500)
    b = cluster_bootstrap_ci(pg, random.Random(20260701), n=500)
    assert a == b                                        # same seed -> identical CI
    assert 0.0 <= a[0] <= a[1] <= 100.0
