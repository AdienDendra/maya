"""Geometry-only opposite-axis detection for joint positions.

This module does not use joint names, hierarchy, or mesh vertices. It estimates
one globally supported opposite axis from a supplied joint-position set, then
recognizes mutual reflected pairs on that axis. The same context can later be
reused by mirroring tools.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


AXIS_NAMES = ("X", "Y", "Z")
MINIMUM_SUPPORTED_PAIRS = 2


@dataclass(frozen=True)
class OppositePair:
    first_index: int
    second_index: int
    mirror_error: float


@dataclass(frozen=True)
class AxisSupport:
    axis: str
    axis_index: int
    pairs: Tuple[OppositePair, ...]
    median_mirror_error: float

    @property
    def pair_count(self) -> int:
        return len(self.pairs)


@dataclass(frozen=True)
class OppositeAxisContext:
    center: Tuple[float, float, float]
    tolerance: float
    axis_supports: Tuple[AxisSupport, ...]
    primary_axis: Optional[str]

    def support_for_axis(self, axis: str) -> Optional[AxisSupport]:
        for support in self.axis_supports:
            if support.axis == axis:
                return support
        return None


def build_opposite_axis_context(
    positions: np.ndarray,
    minimum_supported_pairs: int = MINIMUM_SUPPORTED_PAIRS,
) -> OppositeAxisContext:
    """Estimate the best globally supported opposite axis for joint positions."""

    points = np.asarray(positions, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("positions must have shape (point_count, 3).")
    if points.shape[0] < 2:
        raise ValueError("At least two points are required.")
    if not np.all(np.isfinite(points)):
        raise ValueError("positions contain non-finite values.")
    if int(minimum_supported_pairs) < 1:
        raise ValueError("minimum_supported_pairs must be at least 1.")

    center = np.median(points, axis=0)
    tolerance = _typical_joint_spacing(points)
    supports = tuple(
        _axis_support(points, center, tolerance, axis_index)
        for axis_index in range(3)
    )

    eligible = [
        support
        for support in supports
        if support.pair_count >= int(minimum_supported_pairs)
    ]
    eligible.sort(
        key=lambda support: (
            -support.pair_count,
            support.median_mirror_error,
            support.axis_index,
        )
    )
    primary_axis = eligible[0].axis if eligible else None

    return OppositeAxisContext(
        center=tuple(float(value) for value in center.tolist()),
        tolerance=float(tolerance),
        axis_supports=supports,
        primary_axis=primary_axis,
    )


def detect_opposite_axis(
    first_index: int,
    second_index: int,
    context: OppositeAxisContext,
) -> Optional[str]:
    """Return the supported opposite axis when the pair is a mutual match."""

    first_index = int(first_index)
    second_index = int(second_index)
    if first_index == second_index or context.primary_axis is None:
        return None

    support = context.support_for_axis(context.primary_axis)
    if support is None:
        return None

    low = min(first_index, second_index)
    high = max(first_index, second_index)
    for pair in support.pairs:
        if pair.first_index == low and pair.second_index == high:
            return support.axis
    return None


def _axis_support(points, center, tolerance, axis_index):
    reflected = points.copy()
    reflected[:, axis_index] = 2.0 * center[axis_index] - points[:, axis_index]

    delta = reflected[:, np.newaxis, :] - points[np.newaxis, :, :]
    distances = np.linalg.norm(delta, axis=2)
    np.fill_diagonal(distances, np.inf)
    nearest = np.argmin(distances, axis=1).astype(np.int32)

    pairs = []
    for first_index, second_index in enumerate(nearest.tolist()):
        if first_index >= second_index:
            continue
        if int(nearest[second_index]) != first_index:
            continue

        first_side = points[first_index, axis_index] - center[axis_index]
        second_side = points[second_index, axis_index] - center[axis_index]
        if first_side * second_side >= 0.0:
            continue

        mirror_error = max(
            float(distances[first_index, second_index]),
            float(distances[second_index, first_index]),
        )
        if mirror_error > tolerance:
            continue

        pairs.append(
            OppositePair(
                first_index=int(first_index),
                second_index=int(second_index),
                mirror_error=float(mirror_error),
            )
        )

    median_error = (
        float(np.median([pair.mirror_error for pair in pairs]))
        if pairs
        else float("inf")
    )
    return AxisSupport(
        axis=AXIS_NAMES[int(axis_index)],
        axis_index=int(axis_index),
        pairs=tuple(pairs),
        median_mirror_error=median_error,
    )


def _typical_joint_spacing(points):
    delta = points[:, np.newaxis, :] - points[np.newaxis, :, :]
    distances = np.linalg.norm(delta, axis=2)
    np.fill_diagonal(distances, np.inf)
    nearest = np.min(distances, axis=1)
    finite_positive = nearest[np.isfinite(nearest) & (nearest > 0.0)]
    if finite_positive.size == 0:
        return 0.0
    return float(np.median(finite_positive))
