"""Configuration for automatic Bind Skin and Add Influence smoothing."""

from dataclasses import dataclass

from ad_skin_tools.bind_smoothing.diffusion import (
    MAXIMUM_ITERATIONS,
    MINIMUM_ITERATIONS,
)


DEFAULT_RELAXATION = 1.0
DEFAULT_MAXIMUM_INFLUENCES = 5
DEFAULT_WEIGHT_EPSILON = 1e-12


@dataclass(frozen=True)
class BindSmoothingOptions:
    """Artist-facing options for the automatic bind-smoothing pipeline."""

    iterations: int = 0
    relaxation: float = DEFAULT_RELAXATION
    maximum_influences: int = DEFAULT_MAXIMUM_INFLUENCES
    weight_epsilon: float = DEFAULT_WEIGHT_EPSILON

    def validated(self) -> "BindSmoothingOptions":
        iterations = int(self.iterations)
        relaxation = float(self.relaxation)
        maximum_influences = int(self.maximum_influences)
        weight_epsilon = float(self.weight_epsilon)

        if (
            iterations < MINIMUM_ITERATIONS
            or iterations > MAXIMUM_ITERATIONS
        ):
            raise ValueError(
                "iterations must be between {} and {}.".format(
                    MINIMUM_ITERATIONS,
                    MAXIMUM_ITERATIONS,
                )
            )
        if not 0.0 <= relaxation <= 1.0:
            raise ValueError(
                "relaxation must be between 0.0 and 1.0."
            )
        if maximum_influences < 1:
            raise ValueError(
                "maximum_influences must be at least 1."
            )
        if weight_epsilon < 0.0:
            raise ValueError(
                "weight_epsilon cannot be negative."
            )

        return BindSmoothingOptions(
            iterations=iterations,
            relaxation=relaxation,
            maximum_influences=maximum_influences,
            weight_epsilon=weight_epsilon,
        )

    def effective_maximum_influences(
        self,
        influence_count: int,
    ) -> int:
        """Return the actual per-vertex limit for this solve.

        Iteration zero preserves the hard one-hot contract. Any positive
        iteration uses at most five influences, or fewer when the supplied
        influence list itself contains fewer than five joints.
        """

        validated = self.validated()
        influence_count = int(influence_count)
        if influence_count < 1:
            raise ValueError(
                "influence_count must be at least 1."
            )
        if validated.iterations == 0:
            return 1
        return min(
            validated.maximum_influences,
            influence_count,
        )
