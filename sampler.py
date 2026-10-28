"""Picks printings so that their prices follow a target distribution.

Most printings are bulk, so picking one uniformly at random would give a
quarter card nearly every round. Instead each round:

  1. draws a target price from the distribution we *want* (a smooth curve
     through the quantiles in TARGET_QUANTILES, interpolated in log-price), and
  2. picks uniformly among the pool printings whose price is closest to it.

Step 2 means the pool's own shape doesn't matter (pruning bulk changes nothing
but variety), only that every price range has some cards in it. The neighbour
window is a fixed width in log-price, widened to at least MIN_NEIGHBOURS cards
where the pool is thin (the very top end).
"""
from __future__ import annotations

import bisect
import math
import random
from typing import Callable, Sequence

PRICE_FLOOR = 0.25

# (cumulative probability, price in USD). Anchors past p90 just keep the top
# tail sensible; the curve between anchors is a monotone cubic in log-price.
TARGET_QUANTILES = [
    (0.00, PRICE_FLOOR),
    (0.10, 0.50),
    (0.25, 4.00),
    (0.50, 20.00),
    (0.75, 80.00),
    (0.90, 300.00),
    (0.97, 1500.00),
    (0.995, 6000.00),
    (1.00, 25000.00),
]

WINDOW_DECADES = 0.02  # +/- ~5% in price
MIN_NEIGHBOURS = 12


def floored(price: float) -> float:
    return max(PRICE_FLOOR, price)


def _pchip(xs: Sequence[float], ys: Sequence[float]) -> Callable[[float], float]:
    """Monotone (Fritsch-Carlson) cubic interpolant: smooth, never overshoots."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def f(x: float) -> float:
        i = min(max(bisect.bisect_right(xs, x) - 1, 0), n - 2)
        t = (x - xs[i]) / h[i]
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])

    return f


_quantile_log = _pchip([p for p, _ in TARGET_QUANTILES], [math.log10(v) for _, v in TARGET_QUANTILES])


def target_price(rng: random.Random) -> float:
    return 10 ** _quantile_log(rng.random())


class PriceSampler:
    def __init__(self, items: Sequence[tuple[str, float]]):
        """items: (printing_id, price) pairs; prices are floored here."""
        pairs = sorted((math.log10(floored(p)), pid) for pid, p in items)
        self.logs = [lp for lp, _ in pairs]
        self.ids = [pid for _, pid in pairs]

    def pick(self, rng: random.Random, exclude: Callable[[str], bool] = lambda pid: False) -> str | None:
        for _attempt in range(50):
            pid = self._pick_near(math.log10(target_price(rng)), rng)
            if pid is not None and not exclude(pid):
                return pid
        return None

    def _pick_near(self, x: float, rng: random.Random) -> str | None:
        n = len(self.logs)
        if n == 0:
            return None
        lo = bisect.bisect_left(self.logs, x - WINDOW_DECADES)
        hi = bisect.bisect_right(self.logs, x + WINDOW_DECADES)
        if hi - lo < MIN_NEIGHBOURS:
            # Grow outward from x, nearest first, until we have enough
            lo = hi = bisect.bisect_left(self.logs, x)
            while hi - lo < min(MIN_NEIGHBOURS, n):
                take_left = lo > 0 and (hi >= n or x - self.logs[lo - 1] <= self.logs[hi] - x)
                if take_left:
                    lo -= 1
                else:
                    hi += 1
        return self.ids[rng.randrange(lo, hi)]


if __name__ == "__main__":
    # Sanity check: realised distribution of picks from a dataset file
    import gzip
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/cards.json.gz"
    with gzip.open(path, "rt") as f:
        data = json.load(f)
    sampler = PriceSampler([(pid, data["printings"][pid]["price"]) for pid in data["pool"]])
    rng = random.Random(0)
    picks = [sampler.pick(rng) for _ in range(20000)]
    prices = sorted(floored(data["printings"][pid]["price"]) for pid in picks)
    for q, want in TARGET_QUANTILES[1:-1]:
        got = prices[int(q * len(prices))]
        print(f"p{q * 100:<4g}: target ${want:>8.2f}   realised ${got:>8.2f}")
    print(f"distinct printings picked: {len(set(picks))} / {len(picks)}")
