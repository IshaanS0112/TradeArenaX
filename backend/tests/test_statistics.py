"""The statistics that decide whether a backtest number means anything."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.statistics import (
    bootstrap_mean_ci,
    bootstrap_sharpe_ci,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    lo_standard_error,
    moments,
    normal_cdf,
    normal_ppf,
    probability_of_backtest_overfitting,
    purged_kfold,
    sharpe_ratio,
)


# ------------------------------------------------------------------- normals
@pytest.mark.parametrize(
    "p, expected",
    [(0.5, 0.0), (0.975, 1.959963985), (0.025, -1.959963985), (0.99, 2.326347874)],
)
def test_normal_ppf_matches_published_quantiles(p, expected):
    assert normal_ppf(p) == pytest.approx(expected, abs=1e-9)


def test_normal_ppf_inverts_the_cdf():
    for x in (-3.5, -1.0, 0.0, 0.7, 2.9):
        assert normal_ppf(normal_cdf(x)) == pytest.approx(x, abs=1e-9)


def test_normal_ppf_rejects_impossible_probabilities():
    with pytest.raises(ValueError):
        normal_ppf(0.0)
    with pytest.raises(ValueError):
        normal_ppf(1.0)


# -------------------------------------------------------------------- Sharpe
def test_sharpe_of_a_flat_series_is_none_not_infinity():
    assert sharpe_ratio([0.01] * 50) is None
    assert sharpe_ratio([0.01]) is None


def test_sharpe_annualises_by_the_square_root_of_time():
    rng = np.random.default_rng(1)
    returns = rng.normal(0.001, 0.01, 2_000)
    per_step = sharpe_ratio(returns)
    annual = sharpe_ratio(returns, steps_per_year=252)
    assert annual == pytest.approx(per_step * math.sqrt(252))


def test_lo_standard_error_shrinks_with_sample_size():
    assert lo_standard_error(1.0, 100) > lo_standard_error(1.0, 10_000)
    assert lo_standard_error(1.0, 1) is None


# ----------------------------------------------------------------- bootstrap
def test_bootstrap_ci_brackets_the_point_estimate():
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0005, 0.01, 1_000)
    result = bootstrap_sharpe_ci(returns, resamples=800, seed=11)

    assert result.lower < result.statistic < result.upper
    assert result.resamples == 800
    assert result.block_length >= 2


def test_bootstrap_ci_covers_the_truth_at_about_the_nominal_rate():
    """A small simulation study: 95% intervals should miss about 5% of the time."""
    truth_rng = np.random.default_rng(17)
    mu, sigma, n = 0.0008, 0.01, 400
    true_sharpe = mu / sigma
    covered = 0
    replications = 120

    for i in range(replications):
        sample = truth_rng.normal(mu, sigma, n)
        result = bootstrap_sharpe_ci(sample, resamples=400, seed=1_000 + i)
        if result.lower <= true_sharpe <= result.upper:
            covered += 1

    coverage = covered / replications
    assert 0.85 <= coverage <= 1.0, f"coverage was {coverage:.2f}, expected about 0.95"


def test_bootstrap_flags_disagreement_with_the_analytic_interval():
    """Strong autocorrelation is exactly where Lo's IID assumption breaks."""
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.01, 1_500)
    autocorrelated = np.zeros_like(noise)
    for i in range(1, noise.size):
        autocorrelated[i] = 0.85 * autocorrelated[i - 1] + noise[i]

    result = bootstrap_sharpe_ci(autocorrelated + 0.002, resamples=600, seed=7)
    assert result.disagreement_flag, "a strongly autocorrelated series should flag"


def test_bootstrap_of_a_degenerate_series_reports_nothing():
    result = bootstrap_sharpe_ci([0.0] * 50)
    assert result.statistic is None and result.lower is None


def test_bootstrap_mean_ci_on_paths_contains_zero_for_noise():
    rng = np.random.default_rng(9)
    pnl = rng.normal(0.0, 50.0, 200)
    mean, lower, upper = bootstrap_mean_ci(pnl, resamples=2_000, seed=4)
    assert lower <= 0.0 <= upper
    assert mean == pytest.approx(pnl.mean())


# ----------------------------------------------------------- deflated Sharpe
def test_expected_max_sharpe_grows_with_the_number_of_trials():
    rng = np.random.default_rng(2)
    small = expected_max_sharpe(rng.normal(0, 1, 5))
    large = expected_max_sharpe(rng.normal(0, 1, 500))
    assert large > small > 0


def _trials_with_fixed_dispersion(n: int, sd: float = 0.05) -> np.ndarray:
    """N trial Sharpes whose *sample* standard deviation is exactly ``sd``."""
    spread = np.linspace(-1.0, 1.0, n)
    return spread / float(np.std(spread, ddof=1)) * sd


def test_dsr_falls_monotonically_with_trial_count_at_a_fixed_sharpe():
    """The headline property: the same Sharpe is worth less after more search."""
    observed = 0.15  # per step
    values = [
        deflated_sharpe_ratio(observed, _trials_with_fixed_dispersion(n), n_observations=1_000)
        for n in (5, 20, 100, 400)
    ]

    assert all(v is not None for v in values)
    assert values == sorted(values, reverse=True), values
    assert values[0] > values[-1]


def test_dsr_falls_as_the_trials_get_more_dispersed():
    """Wider search, higher bar: a lucky maximum is more likely."""
    tight = deflated_sharpe_ratio(0.15, _trials_with_fixed_dispersion(50, 0.01), 1_000)
    wide = deflated_sharpe_ratio(0.15, _trials_with_fixed_dispersion(50, 0.08), 1_000)
    assert tight > wide


def test_dsr_is_high_when_the_edge_is_real_and_the_search_was_small():
    trials = [0.01, 0.0, -0.005, 0.004]
    assert deflated_sharpe_ratio(0.25, trials, n_observations=2_000) > 0.95


def test_dsr_needs_a_sample():
    assert deflated_sharpe_ratio(1.0, [0.1, 0.2], n_observations=1) is None


def test_moments_of_a_normal_series_are_zero_skew_and_three_kurtosis():
    rng = np.random.default_rng(21)
    skew, kurt = moments(rng.normal(0, 1, 50_000))
    assert skew == pytest.approx(0.0, abs=0.05)
    assert kurt == pytest.approx(3.0, abs=0.1)


# ------------------------------------------------------------------------ PBO
def test_pbo_is_about_one_half_for_pure_noise():
    """Selecting among noise has no out-of-sample value, and PBO says."""
    rng = np.random.default_rng(31)
    performance = rng.normal(0, 0.01, size=(600, 12))
    result = probability_of_backtest_overfitting(performance, n_blocks=8)

    assert result.splits == 70  # C(8, 4)
    assert 0.3 <= result.pbo <= 0.7, result.pbo


def test_pbo_is_low_when_one_strategy_genuinely_dominates():
    rng = np.random.default_rng(33)
    performance = rng.normal(0, 0.01, size=(600, 8))
    performance[:, 3] += 0.02  # a real, persistent edge
    result = probability_of_backtest_overfitting(performance, n_blocks=8)

    assert result.pbo < 0.1, result.pbo


def test_pbo_rejects_odd_block_counts_and_single_strategies():
    rng = np.random.default_rng(35)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(rng.normal(size=(100, 4)), n_blocks=7)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(rng.normal(size=(100, 1)))


# -------------------------------------------------------------- purged k-fold
def test_purged_kfold_removes_exactly_the_overlapping_observations():
    folds = purged_kfold(n_observations=20, n_folds=4, eval_window=3, embargo=0)
    fold = folds[1]  # test = 5..9

    assert fold.test == [5, 6, 7, 8, 9]
    # 3 and 4 are evaluated over windows that reach into the test fold.
    assert 4 not in fold.train and 3 not in fold.train
    assert 2 in fold.train, "an observation whose window stops short is kept"
    assert 10 in fold.train, "with no embargo, the observation after the fold stays"


def test_embargo_drops_observations_after_the_test_fold():
    folds = purged_kfold(n_observations=20, n_folds=4, eval_window=1, embargo=2)
    fold = folds[1]
    assert fold.test == [5, 6, 7, 8, 9]
    assert 10 not in fold.train and 11 not in fold.train
    assert 12 in fold.train


def test_every_fold_is_disjoint_from_its_own_test_set():
    folds = purged_kfold(n_observations=50, n_folds=5, eval_window=4, embargo=3)
    seen: set[int] = set()
    for fold in folds:
        assert not set(fold.train) & set(fold.test)
        seen |= set(fold.test)
    assert seen == set(range(50)), "every observation is tested exactly once"


def test_purged_kfold_validates_its_arguments():
    with pytest.raises(ValueError):
        purged_kfold(100, n_folds=1)
    with pytest.raises(ValueError):
        purged_kfold(100, n_folds=5, eval_window=0)
