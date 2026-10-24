"""Small shared helpers for the numbers the API and the evaluation gate report."""
import os
from typing import Iterable, Optional, Tuple

import numpy as np


def latency_percentiles(values: Iterable[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    """
    (p50, p95) of the inference latencies, ignoring None (warm-up requests record no latency).
    The median and 95th percentile describe what a request usually costs and what a slow one
    costs; a mean would let one cold-start outlier distort both.
    """
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None, None
    p50, p95 = np.percentile(clean, [50, 95])
    return round(float(p50), 2), round(float(p95), 2)


def ratio_setting(name: str, default: float) -> float:
    """A positive float from the environment; anything missing or unusable falls back to `default`."""
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default
