from scripts.benchmark_cancel_latency import CancellationSample, summarize


def sample(run: int, response: float, convergence: float) -> CancellationSample:
    return CancellationSample(
        run=run,
        run_id=f"run-{run}",
        cancel_response_ms=response,
        convergence_ms=convergence,
        durable_convergence_ms=convergence - response,
        status_polls=1,
        cancelling_sequence=4,
        terminal_sequence=5,
    )


def test_cancel_benchmark_uses_nearest_rank_percentiles() -> None:
    result = summarize(
        [
            sample(1, 1, 10),
            sample(2, 2, 20),
            sample(3, 3, 30),
            sample(4, 4, 40),
            sample(5, 100, 500),
        ]
    )

    assert result["cancel_response_ms"]["p50"] == 3
    assert result["cancel_response_ms"]["p95"] == 100
    assert result["convergence_ms"]["p50"] == 30
    assert result["convergence_ms"]["p95"] == 500
    assert result["durable_convergence_ms"]["p50"] == 27
    assert result["durable_convergence_ms"]["p95"] == 400
