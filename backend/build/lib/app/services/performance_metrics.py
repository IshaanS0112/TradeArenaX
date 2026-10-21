"""Risk-adjusted performance metrics.

Two places where the textbook one-liners are wrong as written, and what this
module does instead:

**Sharpe needs a return series, and a PnL series is not one.** ``(mean_return -
rf) / std_dev`` is dimensionless only if the numerator is a return. PnL is
currency. Dividing PnL differences by nothing produces a number that scales with
position size and cannot be compared to any published Sharpe. Here the PnL
series is converted to returns against an equity curve (``capital_base + PnL``),
and the result is annualised by ``sqrt(steps_per_year)`` so a one-minute-step
Sharpe is comparable to a daily-step one.

**Max drawdown as a percentage of peak PnL breaks when PnL is negative.** If the
peak PnL of a run is -50 and the trough is -200, ``(peak - trough) / peak`` is
-300%: a negative drawdown, which is meaningless. Drawdown is computed on the
equity curve, whose peak is bounded below by the capital base and therefore
positive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

_EPS = 1e-12


@dataclass
class PerformanceSummary:
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    final_inventory: float
    sharpe_ratio: float | None
    sharpe_ratio_per_step: float | None
    sortino_ratio: float | None
    max_drawdown_pct: float | None
    max_drawdown_abs: float | None
    volatility_annualised: float | None
    win_rate: float | None
    closed_round_trips: int
    fill_count: int
    total_return_pct: float
    peak_inventory_abs: float
    max_inventory_risk_score: float | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total_pnl": _r(self.total_pnl),
            "realized_pnl": _r(self.realized_pnl),
            "unrealized_pnl": _r(self.unrealized_pnl),
            "fees_paid": _r(self.fees_paid),
            "final_inventory": _r(self.final_inventory),
            "sharpe_ratio": _r(self.sharpe_ratio),
            "sharpe_ratio_per_step": _r(self.sharpe_ratio_per_step, 8),
            "sortino_ratio": _r(self.sortino_ratio),
            "max_drawdown_pct": _r(self.max_drawdown_pct),
            "max_drawdown_abs": _r(self.max_drawdown_abs),
            "volatility_annualised": _r(self.volatility_annualised),
            "win_rate": _r(self.win_rate, 4),
            "closed_round_trips": self.closed_round_trips,
            "fill_count": self.fill_count,
            "total_return_pct": _r(self.total_return_pct),
            "peak_inventory_abs": _r(self.peak_inventory_abs),
            "max_inventory_risk_score": _r(self.max_inventory_risk_score, 4),
            "notes": self.notes,
        }


def _r(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def equity_curve(pnl_series: Sequence[float], capital_base: float) -> np.ndarray:
    return capital_base + np.asarray(pnl_series, dtype=float)


def step_returns(pnl_series: Sequence[float], capital_base: float) -> np.ndarray:
    """Per-step simple returns on the equity curve.

    A step where equity has gone non-positive yields a 0.0 return rather than a
    division blow-up: the agent is bankrupt, and the correct statement is "no
    further return is defined", not ``inf``.
    """
    equity = equity_curve(pnl_series, capital_base)
    if equity.size < 2:
        return np.empty(0, dtype=float)
    prev, curr = equity[:-1], equity[1:]
    out = np.zeros_like(curr, dtype=float)
    ok = prev > _EPS
    out[ok] = (curr[ok] - prev[ok]) / prev[ok]
    return out


def sharpe_ratio(
    pnl_series: Sequence[float],
    capital_base: float,
    risk_free_per_step: float = 0.0,
    steps_per_year: int | None = None,
) -> tuple[float | None, float | None]:
    """Return ``(annualised_sharpe, per_step_sharpe)``.

    ``None`` when there are fewer than two returns, or when the return series
    has zero dispersion. A zero denominator is not an infinitely good strategy;
    it is an agent that never traded.
    """
    rets = step_returns(pnl_series, capital_base)
    if rets.size < 2:
        return None, None
    excess = rets - risk_free_per_step
    sd = float(np.std(excess, ddof=1))
    if sd <= _EPS:
        return None, None
    per_step = float(np.mean(excess)) / sd
    if steps_per_year is None:
        return None, per_step
    return per_step * math.sqrt(steps_per_year), per_step


def sortino_ratio(
    pnl_series: Sequence[float],
    capital_base: float,
    risk_free_per_step: float = 0.0,
    steps_per_year: int | None = None,
) -> float | None:
    """Sharpe with only downside dispersion in the denominator.

    Included because a market maker's return distribution is deliberately
    asymmetric - many small spread captures, occasional large adverse-selection
    losses - and Sharpe penalises the upside tail of a momentum agent as if it
    were risk.
    """
    rets = step_returns(pnl_series, capital_base)
    if rets.size < 2:
        return None
    excess = rets - risk_free_per_step
    downside = excess[excess < 0]
    if downside.size < 2:
        return None
    dd = float(np.sqrt(np.mean(np.square(downside))))
    if dd <= _EPS:
        return None
    ratio = float(np.mean(excess)) / dd
    return ratio * math.sqrt(steps_per_year) if steps_per_year else ratio


def max_drawdown(
    pnl_series: Sequence[float], capital_base: float
) -> tuple[float | None, float | None]:
    """Largest peak-to-trough decline of the equity curve.

    Returns ``(pct_of_peak, absolute)``. Peak is taken over the running maximum,
    so the reported drawdown is the worst one experienced, not the decline from
    the final high.
    """
    equity = equity_curve(pnl_series, capital_base)
    if equity.size < 2:
        return None, None
    running_peak = np.maximum.accumulate(equity)
    drawdowns = running_peak - equity
    idx = int(np.argmax(drawdowns))
    abs_dd = float(drawdowns[idx])
    peak = float(running_peak[idx])
    pct = (abs_dd / peak) * 100.0 if peak > _EPS else None
    return pct, abs_dd


def annualised_volatility(
    pnl_series: Sequence[float], capital_base: float, steps_per_year: int
) -> float | None:
    rets = step_returns(pnl_series, capital_base)
    if rets.size < 2:
        return None
    return float(np.std(rets, ddof=1)) * math.sqrt(steps_per_year)


def summarise(
    *,
    pnl_series: Sequence[float],
    inventory_series: Sequence[float],
    realized_pnl: float,
    unrealized_pnl: float,
    fees_paid: float,
    win_rate: float | None,
    closed_round_trips: int,
    fill_count: int,
    capital_base: float,
    risk_free_per_step: float,
    steps_per_year: int,
    max_inventory: float | None = None,
) -> PerformanceSummary:
    notes: list[str] = []
    total = float(pnl_series[-1]) if len(pnl_series) else 0.0

    sharpe, sharpe_step = sharpe_ratio(
        pnl_series, capital_base, risk_free_per_step, steps_per_year
    )
    if sharpe is None and len(pnl_series) >= 3:
        notes.append(
            "Sharpe undefined: the PnL series has no step-to-step dispersion, "
            "which means the agent effectively did not trade."
        )

    dd_pct, dd_abs = max_drawdown(pnl_series, capital_base)
    inv = np.abs(np.asarray(inventory_series, dtype=float)) if len(inventory_series) else None
    peak_inv = float(inv.max()) if inv is not None and inv.size else 0.0

    risk_score = None
    if max_inventory:
        risk_score = peak_inv / max_inventory
        if risk_score > 1.0:
            notes.append(
                f"Inventory limit breached: peak |inventory| {peak_inv:.1f} against a "
                f"limit of {max_inventory:.1f} (risk score {risk_score:.2f})."
            )

    if closed_round_trips == 0:
        notes.append(
            "No closed round trips: win rate is undefined and all PnL is unrealized."
        )

    # Annualising a Sharpe ratio from a sample shorter than a trading day is
    # extrapolation by a factor of hundreds, and it produces figures (40, 60)
    # that no real strategy sustains. The number is still reported - the formula
    # is the standard one - but it is labelled rather than left to impress.
    steps_in_sample = len(pnl_series)
    steps_per_day = max(1, steps_per_year // 252)
    if sharpe is not None and steps_in_sample < steps_per_day:
        notes.append(
            f"Annualised Sharpe extrapolates a {steps_in_sample}-step sample "
            f"(under one trading day of {steps_per_day} steps) by a factor of "
            f"sqrt({steps_per_year}) ~= {math.sqrt(steps_per_year):.0f}. Compare "
            "agents on sharpe_ratio_per_step, and treat the annualised figure as "
            "indicative only."
        )

    return PerformanceSummary(
        total_pnl=total,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        fees_paid=fees_paid,
        final_inventory=float(inventory_series[-1]) if len(inventory_series) else 0.0,
        sharpe_ratio=sharpe,
        sharpe_ratio_per_step=sharpe_step,
        sortino_ratio=sortino_ratio(
            pnl_series, capital_base, risk_free_per_step, steps_per_year
        ),
        max_drawdown_pct=dd_pct,
        max_drawdown_abs=dd_abs,
        volatility_annualised=annualised_volatility(pnl_series, capital_base, steps_per_year),
        win_rate=win_rate,
        closed_round_trips=closed_round_trips,
        fill_count=fill_count,
        total_return_pct=(total / capital_base) * 100.0,
        peak_inventory_abs=peak_inv,
        max_inventory_risk_score=risk_score,
        notes=notes,
    )
