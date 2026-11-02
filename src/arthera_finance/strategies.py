"""Ichimoku state and a walk-forward test of the moving-average crossover, over
caller-supplied prices.

Ported from aria-code's local finance tools, with these corrections:
- Walk-forward: each test window recomputed its moving averages from the
  window's own first bar, so the first `slow` bars of every test had no
  signal; drawdown was divided by the last peak instead of the running peak;
  costs were ignored. Here indicators run over the whole causal history, a
  position decided on bar t earns bar t+1's return, and every position change
  pays the commission.
- Ichimoku: the lagging span compared the wrong bars, and the result carried
  trade labels ("strong bull"). Here it reports the state of each component.
Standard library only.
"""
from __future__ import annotations

import math

from .analytics import _mean, _r, _series, _stdev

FAST = (5, 10, 15, 20)
SLOW = (20, 30, 40, 60)


def _midpoint(highs, lows, end, n):
    """Midpoint of the highest high and lowest low over the n bars ending at index end."""
    if end + 1 < n:
        return None
    window = slice(end + 1 - n, end + 1)
    return (max(highs[window]) + min(lows[window])) / 2


def ichimoku(highs, lows, closes):
    close = _series(closes, "closes", 78)
    high = _series(highs, "highs", len(close))
    low = _series(lows, "lows", len(close))
    if not len(high) == len(low) == len(close):
        raise ValueError("highs, lows and closes need one value per bar")
    if any(h < l for h, l in zip(high, low)):
        raise ValueError("every high must be at least the bar's low")
    last = len(close) - 1
    tenkan = _midpoint(high, low, last, 9)
    kijun = _midpoint(high, low, last, 26)
    # The cloud under today's bar was projected 26 bars ago.
    then = last - 26
    span_a = (_midpoint(high, low, then, 9) + _midpoint(high, low, then, 26)) / 2
    span_b = _midpoint(high, low, then, 52)
    top, bottom = max(span_a, span_b), min(span_a, span_b)
    price = close[-1]
    position = "above" if price > top else "below" if price < bottom else "inside"
    # The lagging span is today's close plotted 26 bars back, against that bar's price.
    lagging = "above" if price > close[then] else "below" if price < close[then] else "equal"
    return {"success": True, "source": "caller-supplied bars",
            "ichimoku": {"price": price, "tenkan_sen": _r(tenkan, 4), "kijun_sen": _r(kijun, 4),
                         "senkou_span_a": _r(span_a, 4), "senkou_span_b": _r(span_b, 4),
                         "cloud_thickness": _r(top - bottom, 4),
                         "price_vs_cloud": position,
                         "tenkan_vs_kijun": "above" if tenkan > kijun else "below" if tenkan < kijun else "equal",
                         "cloud": "rising" if span_a > span_b else "falling" if span_a < span_b else "flat",
                         "lagging_span_vs_price_26_bars_ago": lagging},
            "limitations": ["Standard 9/26/52 periods on the supplied bars; the last bar is treated as today.",
                            "These are the components' states, not a trade signal."]}


def _moving_averages(close, n):
    """Causal simple moving average at every index; None before n bars exist."""
    out, total = [None] * len(close), 0.0
    for i, value in enumerate(close):
        total += value
        if i >= n:
            total -= close[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def _segment(close, positions, start, end, fee):
    """Daily strategy returns for bars start..end-1, each earned by the position of the bar before."""
    rets = []
    for t in range(max(start, 1), end):
        held = positions[t - 1]
        before = positions[t - 2] if t >= 2 else 0
        rets.append(held * (close[t] / close[t - 1] - 1) - fee * abs(held - before))
    return rets


def _stats(rets):
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = max(worst, 1 - equity / peak)
    sd = _stdev(rets) if len(rets) > 1 else 0.0
    sharpe = _mean(rets) / sd * math.sqrt(252) if sd > 0 else None
    return equity - 1, sharpe, worst


def walk_forward(closes, windows=5, train_ratio=0.7, commission=0.0005):
    close = _series(closes, "closes", 200)
    if type(windows) is not int or not 2 <= windows <= 20:
        raise ValueError("windows must be an integer from 2 to 20")
    for name, value, low, high in (("train_ratio", train_ratio, 0.5, 0.9), ("commission", commission, 0, 0.01)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{name} must be a number from {low} to {high}")
    size = len(close) // windows
    if size * (1 - train_ratio) < 20:
        raise ValueError("each test window needs at least 20 bars: supply more closes or fewer windows")
    averages = {n: _moving_averages(close, n) for n in set(FAST) | set(SLOW)}
    signals = {}
    for fast in FAST:
        for slow in SLOW:
            if fast < slow:
                f, s = averages[fast], averages[slow]
                signals[(fast, slow)] = [1 if f[i] is not None and s[i] is not None and f[i] > s[i] else 0
                                         for i in range(len(close))]
    results = []
    for w in range(windows):
        start = w * size
        end = start + size if w < windows - 1 else len(close)
        split = start + int((end - start) * train_ratio)
        best, best_sharpe = None, None
        for params, positions in signals.items():
            _, sharpe, _ = _stats(_segment(close, positions, start, split, commission))
            if sharpe is not None and (best_sharpe is None or sharpe > best_sharpe):
                best, best_sharpe = params, sharpe
        if best is None:  # no parameter traded in training: keep the default
            best = (10, 30)
        total, sharpe, drawdown = _stats(_segment(close, signals[best], split, end, commission))
        hold, _, _ = _stats(_segment(close, [1] * len(close), split, end, 0))
        results.append({"window": w + 1, "train_bars": split - start, "test_bars": end - split,
                        "fast": best[0], "slow": best[1], "train_sharpe": _r(best_sharpe, 3),
                        "test_return": _r(total), "buy_hold_return": _r(hold),
                        "excess_return": _r(total - hold), "test_sharpe": _r(sharpe, 3),
                        "test_max_drawdown": _r(drawdown)})
    n = len(results)
    summary = {"windows": n,
               "mean_test_return": _r(_mean([r["test_return"] for r in results])),
               "mean_excess_return": _r(_mean([r["excess_return"] for r in results])),
               "windows_with_positive_return": sum(r["test_return"] > 0 for r in results),
               "windows_beating_buy_hold": sum(r["excess_return"] > 0 for r in results),
               "distinct_parameter_choices": len({(r["fast"], r["slow"]) for r in results})}
    return {"success": True, "strategy": "sma_cross", "summary": summary, "windows": results,
            "source": "caller-supplied closes",
            "limitations": ["Long or flat only; a position decided on a bar's close earns the next bar's return.",
                            f"Each position change pays commission {commission}; slippage and taxes are not modelled.",
                            "Parameters are chosen by in-sample Sharpe from a fixed grid; many distinct choices across windows suggest the edge is not stable.",
                            "Past windows do not predict future returns."]}
