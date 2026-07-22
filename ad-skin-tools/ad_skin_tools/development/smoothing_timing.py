"""Development-only timing report for the smoothing hot path."""


def print_timing(result) -> None:
    smoothing = result.smoothing_result
    if smoothing is None:
        return

    diffusion = smoothing.diffusion_result
    print("\n[AD Skin Tool - Smoothing Development Timing]")
    print("Blend:", result.smoothing_blend)
    print("Iterations:", result.smoothing_iterations)
    print("Mixed vertices:", result.smoothing_mixed_vertex_count)
    print("Input validation:", round(smoothing.input_validation_seconds, 6))
    print("Diffusion total:", round(smoothing.diffusion_seconds, 6))
    print(
        "  topology setup:",
        round(diffusion.topology_setup_seconds, 6),
    )
    print(
        "  neighbour averaging iterations:",
        round(diffusion.iteration_seconds, 6),
    )
    print(
        "  diffusion finalization:",
        round(diffusion.finalization_seconds, 6),
    )
    print(
        "Max Influences projection:",
        round(smoothing.maximum_influence_seconds, 6),
    )
    print(
        "Blocking-owner maximum:",
        round(smoothing.owner_maximum_seconds, 6),
    )
    print("Final validation:", round(smoothing.validation_seconds, 6))
    print("Final assembly:", round(smoothing.assembly_seconds, 6))
    print("Smoothing solver total:", round(smoothing.elapsed_seconds, 6))
