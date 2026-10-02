"""Statistics for backtests that are about to be believed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Sequence

import numpy as np

EULER_MASCHERONI = 0.5772156649015329


# --------------------------------------------------------------------- normals
def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation, refined once)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"normal_ppf needs 0 < p < 1, got {p}")

    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)

    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
        )
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )

    error = normal_cdf(x) - p
    density = math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    if density > 0:
        u = error / density
        x = x - u / (1 + 0.5 * x * u)
    return x


# ---------------------------------------------------------------------- Sharpe
def sharpe_ratio(
    returns: Sequence[float],
    risk_free_per_step: float = 0.0,
    steps_per_year: float | None = None,
) -> float | None:
    """Sharpe of a return series, annualised when a step count is given."""
    array = np.asarray(list(returns), dtype=float)
    if array.size < 2:
        return None
    excess = array - risk_free_per_step
    sd = float(np.std(excess, ddof=1))
    if sd < 1e-15:
        return None
    ratio = float(np.mean(excess)) / sd
    if steps_per_year:
        ratio *= math.sqrt(steps_per_year)
    return ratio


def lo_standard_error(sharpe: float, n_observations: int) -> float | None:
    """Lo (2002) analytic SE of a Sharpe ratio, under IID returns."""
    if n_observations < 2:
        return None
    return math.sqrt((1.0 + 0.5 * sharpe**2) / n_observations)


# ------------------------------------------------------------------- bootstrap
@dataclass(slots=True)
class BootstrapResult:
    statistic: float | None
    lower: float | None
    upper: float | None
    resamples: int
    block_length: float
    alpha: float
    # Lo's analytic SE and the implied interval, for comparison.
    analytic_se: float | None
    analytic_lower: float | None
    analytic_upper: float | None
    # True when the two intervals disagree by more than 25% in width, which is a statement about.
    disagreement_flag: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "statistic": self.statistic,
            "ci_lower": self.lower,
            "ci_upper": self.upper,
            "resamples": self.resamples,
            "block_length": self.block_length,
            "alpha": self.alpha,
            "analytic_se": self.analytic_se,
            "analytic_ci_lower": self.analytic_lower,
            "analytic_ci_upper": self.analytic_upper,
            "bootstrap_analytic_disagreement": self.disagreement_flag,
        }


def stationary_bootstrap_indices(
    n: int, block_length: float, rng: np.random.Generator
) -> np.ndarray:
    """One stationary-bootstrap resample of positions 0..n-1."""
    if n <= 0:
        return np.empty(0, dtype=int)
    p = 1.0 / max(block_length, 1.0)
    indices = np.empty(n, dtype=int)
    current = int(rng.integers(0, n))
    for i in range(n):
        indices[i] = current
        if rng.random() < p:
            current = int(rng.integers(0, n))
        else:
            current = (current + 1) % n
    return indices


def bootstrap_sharpe_ci(
    returns: Sequence[float],
    risk_free_per_step: float = 0.0,
    steps_per_year: float | None = None,
    resamples: int = 10_000,
    block_length: float | None = None,
    alpha: float = 0.05,
    seed: int = 12345,
) -> BootstrapResult:
    """Percentile CI on the Sharpe ratio by stationary bootstrap."""
    array = np.asarray(list(returns), dtype=float)
    point = sharpe_ratio(array, risk_free_per_step, steps_per_year)
    n = array.size
    # Politis-White's rule of thumb scaled to this series length.
    length = block_length if block_length is not None else max(2.0, n ** (1 / 3))

    if point is None or n < 8:
        return BootstrapResult(
            statistic=point,
            lower=None,
            upper=None,
            resamples=0,
            block_length=length,
            alpha=alpha,
            analytic_se=None,
            analytic_lower=None,
            analytic_upper=None,
            disagreement_flag=False,
        )

    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(resamples):
        sample = array[stationary_bootstrap_indices(n, length, rng)]
        value = sharpe_ratio(sample, risk_free_per_step, steps_per_year)
        if value is not None:
            draws.append(value)

    lower = float(np.percentile(draws, 100 * alpha / 2))
    upper = float(np.percentile(draws, 100 * (1 - alpha / 2)))

    se = lo_standard_error(point, n)
    if steps_per_year and se is not None:
        se *= math.sqrt(steps_per_year)
    z = normal_ppf(1 - alpha / 2)
    analytic_lower = None if se is None else point - z * se
    analytic_upper = None if se is None else point + z * se

    disagreement = False
    if se is not None:
        bootstrap_width = upper - lower
        analytic_width = 2 * z * se
        if analytic_width > 0:
            disagreement = abs(bootstrap_width - analytic_width) / analytic_width > 0.25

    return BootstrapResult(
        statistic=point,
        lower=lower,
        upper=upper,
        resamples=len(draws),
        block_length=length,
        alpha=alpha,
        analytic_se=se,
        analytic_lower=analytic_lower,
        analytic_upper=analytic_upper,
        disagreement_flag=disagreement,
    )


def bootstrap_mean_ci(
    values: Sequence[float],
    resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 12345,
) -> tuple[float | None, float | None, float | None]:
    """IID percentile CI on a mean across *paths*."""
    array = np.asarray(list(values), dtype=float)
    if array.size < 2:
        return (float(array[0]) if array.size else None, None, None)
    rng = np.random.default_rng(seed)
    means = rng.choice(array, size=(resamples, array.size), replace=True).mean(axis=1)
    return (
        float(array.mean()),
        float(np.percentile(means, 100 * alpha / 2)),
        float(np.percentile(means, 100 * (1 - alpha / 2))),
    )


# ------------------------------------------------------------ deflated Sharpe
def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float:
    """The Sharpe a *lucky* search would produce from strategies with no edge."""
    array = np.asarray(list(trial_sharpes), dtype=float)
    n = array.size
    if n < 2:
        return 0.0
    variance = float(np.var(array, ddof=1))
    if variance <= 0:
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER_MASCHERONI) * normal_ppf(1 - 1 / n)
        + EULER_MASCHERONI * normal_ppf(1 - 1 / (n * math.e))
    )


def deflated_sharpe_ratio(
    observed_sharpe: float,
    trial_sharpes: Sequence[float],
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float | None:
    """Probability that the true Sharpe exceeds zero, given the search."""
    if n_observations < 2:
        return None
    sr0 = expected_max_sharpe(trial_sharpes)
    denominator = 1.0 - skewness * observed_sharpe + 0.25 * (kurtosis - 1.0) * observed_sharpe**2
    if denominator <= 0:
        # Heavy enough tails that the estimator is not defined; saying nothing is the honest answer.
        return None
    z = (observed_sharpe - sr0) * math.sqrt(n_observations - 1) / math.sqrt(denominator)
    return normal_cdf(z)


def moments(returns: Sequence[float]) -> tuple[float, float]:
    """Sample skewness and (non-excess) kurtosis of a return series."""
    array = np.asarray(list(returns), dtype=float)
    if array.size < 3:
        return 0.0, 3.0
    centred = array - array.mean()
    sd = float(np.sqrt(np.mean(centred**2)))
    if sd < 1e-15:
        return 0.0, 3.0
    skew = float(np.mean(centred**3) / sd**3)
    kurt = float(np.mean(centred**4) / sd**4)
    return skew, kurt


# ----------------------------------------------------------------------- PBO
@dataclass(slots=True)
class PBOResult:
    pbo: float | None
    # Logits of the out-of-sample relative rank of each in-sample winner.
    logits: list[float]
    splits: int
    n_strategies: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "pbo": self.pbo,
            "splits": self.splits,
            "n_strategies": self.n_strategies,
            "median_logit": float(np.median(self.logits)) if self.logits else None,
        }


def probability_of_backtest_overfitting(
    performance: np.ndarray,
    n_blocks: int = 10,
    statistic: Callable[[np.ndarray], float] | None = None,
) -> PBOResult:
    """CSCV: how often the in-sample winner is below median out-of-sample."""
    matrix = np.asarray(performance, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("performance must be (observations x strategies) with S >= 2")
    if n_blocks % 2 != 0:
        raise ValueError("n_blocks must be even so the splits are balanced")

    score = statistic or (lambda column: float(np.mean(column)) / (float(np.std(column)) + 1e-12))

    blocks = np.array_split(matrix, n_blocks, axis=0)
    n_strategies = matrix.shape[1]
    logits: list[float] = []

    for chosen in combinations(range(n_blocks), n_blocks // 2):
        in_sample = np.vstack([blocks[i] for i in chosen])
        out_sample = np.vstack([blocks[i] for i in range(n_blocks) if i not in chosen])

        in_scores = np.array([score(in_sample[:, s]) for s in range(n_strategies)])
        out_scores = np.array([score(out_sample[:, s]) for s in range(n_strategies)])

        winner = int(np.argmax(in_scores))
        # Relative rank of the winner out-of-sample, in (0, 1).
        rank = float((out_scores < out_scores[winner]).sum() + 1) / (n_strategies + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))

    if not logits:
        return PBOResult(pbo=None, logits=[], splits=0, n_strategies=n_strategies)

    pbo = float(np.mean([1.0 if value <= 0 else 0.0 for value in logits]))
    return PBOResult(pbo=pbo, logits=logits, splits=len(logits), n_strategies=n_strategies)


# ------------------------------------------------------------ purged k-fold CV
@dataclass(slots=True)
class Fold:
    train: list[int]
    test: list[int]


def purged_kfold(
    n_observations: int,
    n_folds: int = 5,
    eval_window: int = 1,
    embargo: int = 0,
) -> list[Fold]:
    """k-fold splits with overlapping observations purged and an embargo."""
    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    if eval_window < 1:
        raise ValueError("eval_window must be at least 1")

    indices = np.arange(n_observations)
    folds: list[Fold] = []
    for test_indices in np.array_split(indices, n_folds):
        if test_indices.size == 0:  # pragma: no cover - tiny n
            continue
        start, end = int(test_indices[0]), int(test_indices[-1])
        # An observation at i is evaluated over [i, i + eval_window - 1], so it overlaps the test fold.
        overlaps_low = start - (eval_window - 1)
        overlaps_high = end + embargo
        train = [i for i in indices if i < overlaps_low or i > overlaps_high]
        folds.append(Fold(train=train, test=[int(i) for i in test_indices]))
    return folds
