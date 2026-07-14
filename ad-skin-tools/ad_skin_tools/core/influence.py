from typing import List

from ad_skin_tools.core.compat import ensure_numpy
np = ensure_numpy()


class InfluenceError(RuntimeError):
    pass


def short_name(node: str) -> str:
    return node.split("|")[-1]


def resolve_influence_indices(
    all_influences: List[str],
    requested_influences: List[str],
) -> np.ndarray:
    """
    Resolve selected influence names to column indices in the skin weight matrix.

    Matching priority:
    1. exact name
    2. short DAG name
    """
    if not requested_influences:
        raise InfluenceError("No influences selected.")

    exact_map = {name: index for index, name in enumerate(all_influences)}
    short_map = {short_name(name): index for index, name in enumerate(all_influences)}

    resolved = []

    for requested in requested_influences:
        if requested in exact_map:
            resolved.append(exact_map[requested])
            continue

        requested_short = short_name(requested)

        if requested_short in short_map:
            resolved.append(short_map[requested_short])
            continue

        raise InfluenceError(f"Influence is not part of skinCluster: {requested}")

    return np.array(sorted(set(resolved)), dtype=np.int32)


def resolve_influence_names(
    all_influences: List[str],
    requested_influences: List[str],
) -> List[str]:
    indices = resolve_influence_indices(all_influences, requested_influences)
    return [all_influences[int(index)] for index in indices]