"""Offline analytics over caller-supplied data: technical factors, portfolio
weights, Piotroski F-score and Altman Z''-score.

Ported from aria-code's local finance tools. There, each tool fetched its own
data and filled gaps with zero, so a missing balance-sheet line could move a
score. Here the caller passes the data (for prices, from financial_market_history),
a signal whose inputs are missing is null, and a verdict is given only when
every signal was evaluated. Standard library only.
"""
from __future__ import annotations

import math
import random
import re

_SYMBOL = re.compile(r"[A-Za-z0-9.^=/:-]{1,32}")


def _series(values, name, minimum_length, positive=True):
    if not isinstance(values, list) or not minimum_length <= len(values) <= 5000:
        raise ValueError(f"{name} requires {minimum_length}..5000 observations")
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} values must be finite numbers")
        if (value <= 0) if positive else (value < 0):
            raise ValueError(f"{name} values must be {'positive' if positive else 'non-negative'}")
        out.append(float(value))
    return out


def _mean(values):
    return sum(values) / len(values)


def _stdev(values):
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _ema(values, span):
    alpha, out = 2 / (span + 1), [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def _r(value, digits=5):
    return None if value is None or not math.isfinite(value) else round(value, digits)


def technical_factors(closes, volumes=None, benchmark_closes=None):
    close = _series(closes, "closes", 20)
    returns = [b / a - 1 for a, b in zip(close, close[1:])]
    f = {"observations": len(close), "last_close": close[-1]}
    for n in (5, 10, 20, 60, 120):
        if len(close) > n:
            f[f"return_{n}d"] = _r(close[-1] / close[-1 - n] - 1)
    for n in (5, 10, 20, 60, 120, 200):
        if len(close) >= n:
            ma = _mean(close[-n:])
            f[f"ma_{n}"] = _r(ma, 4)
            f[f"ma_{n}_gap"] = _r(close[-1] / ma - 1)
    for n in (10, 20, 60):
        if len(returns) >= n:
            f[f"volatility_{n}d"] = _r(_stdev(returns[-n:]) * math.sqrt(252))
    # 14-day RSI on simple averages, as aria-code's fallback computed it.
    deltas = [b - a for a, b in zip(close, close[1:])][-14:]
    gain = _mean([max(d, 0) for d in deltas])
    loss = _mean([max(-d, 0) for d in deltas])
    f["rsi_14"] = None if loss == 0 and gain == 0 else 100.0 if loss == 0 else _r(100 - 100 / (1 + gain / loss), 2)
    if len(close) >= 26:
        macd = [a - b for a, b in zip(_ema(close, 12), _ema(close, 26))]
        signal = _ema(macd, 9)
        f.update(macd=_r(macd[-1]), macd_signal=_r(signal[-1]), macd_hist=_r(macd[-1] - signal[-1]))
    window = close[-20:]
    mid, sd = _mean(window), _stdev(window)
    upper, lower = mid + 2 * sd, mid - 2 * sd
    f.update(bb_upper=_r(upper, 4), bb_lower=_r(lower, 4),
             bb_position=_r((close[-1] - lower) / (upper - lower), 4) if upper > lower else None)
    if volumes is not None:
        volume = _series(volumes, "volumes", len(close), positive=False)
        if len(volume) != len(close):
            raise ValueError("volumes must have one value per close")
        average = _mean(volume[-20:])
        f["volume_ratio_20d"] = _r(volume[-1] / average, 3) if average > 0 else None
    if benchmark_closes is not None:
        bench = _series(benchmark_closes, "benchmark_closes", len(close))
        if len(bench) != len(close):
            raise ValueError("benchmark_closes must have one value per close")
        if len(returns) >= 30:
            a = returns[-60:]
            b = [y / x - 1 for x, y in zip(bench, bench[1:])][-60:]
            ma, mb = _mean(a), _mean(b)
            var = sum((y - mb) ** 2 for y in b)
            f["beta"] = _r(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / var, 3) if var > 0 else None
    gaps = [(f.get(k), w) for k, w in (("ma_5_gap", 0.15), ("ma_20_gap", 0.35), ("ma_60_gap", 0.50))]
    f["trend_score"] = None if any(g is None for g, _ in gaps) else round(sum(w if g > 0 else -w for g, w in gaps), 3)
    return {"success": True, "factors": f, "source": "caller-supplied closes",
            "limitations": ["Factors describe the supplied series only; the last value is treated as the latest close.",
                            "Volatility is annualised with 252 periods; beta uses up to the last 60 aligned returns.",
                            "trend_score is null until 60 closes are supplied."]}


def _solve(matrix, vector):
    """Gauss-Jordan with partial pivoting; matrix is small (at most 20x20)."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-15:
            raise ValueError("The covariance matrix is singular; remove duplicated or constant series")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                a[r] = [x - factor * y for x, y in zip(a[r], a[col])]
    return [a[i][n] / a[i][i] for i in range(n)]


def optimize_weights(prices, method="max_sharpe", risk_free_rate=0.04, periods_per_year=252):
    if not isinstance(prices, dict) or not 2 <= len(prices) <= 20:
        raise ValueError("prices requires 2..20 symbols")
    if method not in ("max_sharpe", "min_var", "equal_weight"):
        raise ValueError("method must be max_sharpe, min_var or equal_weight")
    if isinstance(risk_free_rate, bool) or not isinstance(risk_free_rate, (int, float)) or not -0.1 <= risk_free_rate <= 0.5:
        raise ValueError("risk_free_rate must be a number from -0.1 to 0.5")
    if type(periods_per_year) is not int or not 1 <= periods_per_year <= 100000:
        raise ValueError("periods_per_year must be an integer from 1 to 100000")
    symbols, series = list(prices), []
    for symbol in symbols:
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) or ".." in symbol or symbol[0] in "./":
            raise ValueError("Invalid symbol")
        closes = _series(prices[symbol], f"prices[{symbol}]", 30)
        if max(closes) == min(closes):
            raise ValueError(f"prices[{symbol}] has no price variation")
        series.append(closes)
    if len({len(s) for s in series}) != 1:
        raise ValueError("Every symbol needs the same number of closes, aligned on the same dates")
    returns = [[math.log(b / a) for a, b in zip(s, s[1:])] for s in series]
    n, k = len(symbols), periods_per_year
    mu = [_mean(r) * k for r in returns]
    means = [_mean(r) for r in returns]
    cov = [[sum((x - means[i]) * (y - means[j]) for x, y in zip(returns[i], returns[j])) / (len(returns[i]) - 1) * k
            for j in range(n)] for i in range(n)]

    def stats(w):
        ret = sum(a * b for a, b in zip(w, mu))
        var = sum(w[i] * w[j] * cov[i][j] for i in range(n) for j in range(n))
        vol = math.sqrt(max(var, 0))
        return ret, vol, (ret - risk_free_rate) / vol if vol > 0 else None

    limitations = ["Estimated from historical log returns; past mean returns are a weak predictor."]
    if method == "equal_weight":
        weights = [1 / n] * n
    elif method == "min_var":
        raw = _solve([[c + (1e-10 if i == j else 0) for j, c in enumerate(row)] for i, row in enumerate(cov)], [1.0] * n)
        total = sum(raw)
        if abs(total) < 1e-15:
            raise ValueError("No minimum-variance solution for these series")
        weights = [w / total for w in raw]
        limitations.append("min_var is the unconstrained analytic solution; negative weights are short positions.")
    else:
        rng, weights, best = random.Random(42), [1 / n] * n, stats([1 / n] * n)[2]
        for _ in range(5000):
            draw = [rng.gammavariate(1, 1) for _ in range(n)]
            total = sum(draw)
            w = [d / total for d in draw]
            sharpe = stats(w)[2]
            if sharpe is not None and (best is None or sharpe > best):
                weights, best = w, sharpe
        limitations.append("max_sharpe is a seeded random search over long-only weights (5,000 draws), not an exact optimiser.")
    ret, vol, sharpe = stats(weights)
    return {"success": True, "method": method, "weights": {s: round(w, 4) for s, w in zip(symbols, weights)},
            "expected_return": _r(ret, 4), "volatility": _r(vol, 4), "sharpe": _r(sharpe, 3) if sharpe is not None else None,
            "observations": len(series[0]), "source": "caller-supplied closes", "limitations": limitations}


_PERIOD_FIELDS = ("net_income", "total_assets", "operating_cash_flow", "long_term_debt", "current_assets",
                  "current_liabilities", "shares_outstanding", "revenue", "gross_profit", "retained_earnings",
                  "ebit", "total_equity", "total_liabilities")


def _period(data, name):
    if data is None:
        return {}
    if not isinstance(data, dict) or len(data) > 40:
        raise ValueError(f"{name} must be an object of statement fields")
    unknown = set(data) - set(_PERIOD_FIELDS)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(unknown))[:200]}")
    out = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name}.{key} must be a finite number")
        out[key] = float(value)
    return out


def _div(a, b):
    return None if a is None or not b else a / b


def _cmp(a, b, better):
    if a is None or b is None:
        return None
    return int(a > b) if better == "up" else int(a < b) if better == "down" else int(a <= b)


def quality_scores(current, prior=None):
    cur, pre = _period(current, "current"), _period(prior, "prior")
    if not cur:
        raise ValueError("current statement fields are required")
    g = lambda p, k: p.get(k)
    roa = _div(g(cur, "net_income"), g(cur, "total_assets"))
    roa_prev = _div(g(pre, "net_income"), g(pre, "total_assets"))
    cfo = g(cur, "operating_cash_flow")
    signals = {
        "F1_roa_positive": None if roa is None else int(roa > 0),
        "F2_cfo_positive": None if cfo is None else int(cfo > 0),
        "F3_roa_increasing": _cmp(roa, roa_prev, "up"),
        "F4_cfo_exceeds_net_income": _cmp(cfo, g(cur, "net_income"), "up"),
        "F5_leverage_lower": _cmp(_div(g(cur, "long_term_debt"), g(cur, "total_assets")),
                                  _div(g(pre, "long_term_debt"), g(pre, "total_assets")), "down"),
        "F6_current_ratio_higher": _cmp(_div(g(cur, "current_assets"), g(cur, "current_liabilities")),
                                        _div(g(pre, "current_assets"), g(pre, "current_liabilities")), "up"),
        "F7_no_dilution": _cmp(g(cur, "shares_outstanding"), g(pre, "shares_outstanding"), "not_up"),
        "F8_gross_margin_higher": _cmp(_div(g(cur, "gross_profit"), g(cur, "revenue")),
                                       _div(g(pre, "gross_profit"), g(pre, "revenue")), "up"),
        "F9_asset_turnover_higher": _cmp(_div(g(cur, "revenue"), g(cur, "total_assets")),
                                         _div(g(pre, "revenue"), g(pre, "total_assets")), "up"),
    }
    known = [v for v in signals.values() if v is not None]
    score = sum(known)
    complete = len(known) == 9
    piotroski = {"f_score": score, "signals_evaluated": len(known), "signals": signals,
                 "verdict": None if not complete else "high" if score >= 7 else "low" if score <= 3 else "neutral"}

    need = ("current_assets", "current_liabilities", "retained_earnings", "ebit", "total_assets",
            "total_equity", "total_liabilities")
    missing = [k for k in need if k not in cur]
    altman = {"variant": "Z'' (non-manufacturers, book equity)", "missing": missing}
    if not missing and cur["total_assets"] > 0 and cur["total_liabilities"] > 0:
        ta = cur["total_assets"]
        x = {"X1_working_capital": (cur["current_assets"] - cur["current_liabilities"]) / ta,
             "X2_retained_earnings": cur["retained_earnings"] / ta,
             "X3_ebit": cur["ebit"] / ta,
             "X4_equity_to_liabilities": cur["total_equity"] / cur["total_liabilities"]}
        z = 6.56 * x["X1_working_capital"] + 3.26 * x["X2_retained_earnings"] + 6.72 * x["X3_ebit"] + 1.05 * x["X4_equity_to_liabilities"]
        altman.update(z_score=round(z, 3), components={k: round(v, 4) for k, v in x.items()},
                      zone="safe" if z > 2.6 else "grey" if z > 1.1 else "distress")
    else:
        altman.update(z_score=None, zone=None)
        if not missing:
            altman["missing"] = ["positive total_assets and total_liabilities"]
    return {"success": True, "piotroski": piotroski, "altman": altman, "source": "caller-supplied statements",
            "limitations": ["Signals with missing inputs are null and not counted; a verdict needs all nine.",
                            "F1 and F3 use period-end total assets, not the average or opening balance.",
                            "Z'' zones (2.6 / 1.1) are the published non-manufacturer cut-offs; they are not a default probability."]}
