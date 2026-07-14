from ad_skin_tools.core.compat import ensure_numpy
np = ensure_numpy()


def clamp01(values: np.ndarray) -> np.ndarray:
    return np.clip(values, 0.0, 1.0)


def normalize_rows(weights: np.ndarray) -> np.ndarray:
    """
    Normalize every vertex row so total influence weight equals 1.0.
    """
    weights = np.asarray(weights, dtype=np.float64)

    row_sum = weights.sum(axis=1, keepdims=True)
    row_sum = np.where(row_sum == 0.0, 1.0, row_sum)

    return weights / row_sum


def blend_by_falloff(
    old_weights: np.ndarray,
    target_weights: np.ndarray,
    falloff: np.ndarray,
    strength: float = 1.0,
) -> np.ndarray:
    """
    Blend old and target weights using Maya soft selection falloff.

    final = old * (1 - mask) + target * mask

    where:
    mask = falloff * strength
    """
    old_weights = np.asarray(old_weights, dtype=np.float64)
    target_weights = np.asarray(target_weights, dtype=np.float64)

    mask = np.asarray(falloff, dtype=np.float64) * float(strength)
    mask = clamp01(mask).reshape(-1, 1)

    return old_weights * (1.0 - mask) + target_weights * mask


def build_even_target(
    old_weights: np.ndarray,
    influence_indices: np.ndarray,
) -> np.ndarray:
    """
    Build target matrix where selected influences share weight evenly.
    """
    target = np.zeros_like(old_weights)

    value = 1.0 / float(len(influence_indices))
    target[:, influence_indices] = value

    return target


def build_closest_target(
    old_weights: np.ndarray,
    closest_influence_indices: np.ndarray,
) -> np.ndarray:
    """
    Build target matrix where each vertex gets 1.0 on its closest influence.
    """
    target = np.zeros_like(old_weights)

    rows = np.arange(old_weights.shape[0])
    target[rows, closest_influence_indices] = 1.0

    return target