"""Small statistics helpers for the evaluation. Seeded, so every table regenerates identically."""

import math
from collections.abc import Sequence

import numpy as np
from scipy.stats import binomtest, mannwhitneyu

SEED = 20261109


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a proportion; (0, 1) when n == 0."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_median_diff(
    a: Sequence[float], b: Sequence[float], reps: int = 10_000, seed: int = SEED
) -> tuple[float, float, float]:
    """(median(a) - median(b), lo, hi): percentile-bootstrap 95 % interval of the difference of medians."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    draws = np.median(rng.choice(x, (reps, len(x))), axis=1) - np.median(rng.choice(y, (reps, len(y))), axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(np.median(x) - np.median(y)), float(lo), float(hi)


def mann_whitney_p(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Two-sided Mann-Whitney U p-value; None if either sample is empty or all values are identical."""
    if not len(a) or not len(b) or len(set(a) | set(b)) == 1:
        return None
    return float(mannwhitneyu(a, b, alternative="two-sided", method="auto").pvalue)


def mcnemar_exact(a_correct: Sequence[bool], b_correct: Sequence[bool]) -> tuple[int, int, float | None]:
    """Paired comparison of two systems on the same incidents: (only A right, only B right, two-sided exact p).

    p is None when the systems never disagree."""
    only_a = sum(1 for a, b in zip(a_correct, b_correct, strict=True) if a and not b)
    only_b = sum(1 for a, b in zip(a_correct, b_correct, strict=True) if b and not a)
    if only_a + only_b == 0:
        return 0, 0, None
    return only_a, only_b, float(binomtest(min(only_a, only_b), only_a + only_b, 0.5).pvalue)


def quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    q1, med, q3 = np.percentile(np.asarray(values, dtype=float), [25, 50, 75])
    return float(q1), float(med), float(q3)


def confusion_matrix(pairs: Sequence[tuple[str, str]], labels: Sequence[str]) -> list[list[int]]:
    """Rows = expected label, columns = predicted label."""
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for expected, predicted in pairs:
        if expected in index and predicted in index:
            matrix[index[expected]][index[predicted]] += 1
    return matrix


def precision_recall(pairs: Sequence[tuple[str, str]], labels: Sequence[str]) -> dict[str, dict[str, float | None]]:
    out = {}
    for label in labels:
        tp = sum(1 for e, p in pairs if e == label and p == label)
        predicted = sum(1 for _, p in pairs if p == label)
        actual = sum(1 for e, _ in pairs if e == label)
        out[label] = {
            "precision": tp / predicted if predicted else None,
            "recall": tp / actual if actual else None,
            "support": actual,
        }
    return out
