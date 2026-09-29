import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_html

from api.client import get_health, quote_body
from api.models import Quote
from tests.helpers import FEE_RATE

pytestmark = pytest.mark.perf

PARALLEL_REQUESTS = 20
# Env var name: PERF (this perf test) + P95 (95th percentile latency) + MS (milliseconds).
# Override it without editing the test, e.g. `PERF_P95_MS=500 uv run pytest -m perf`.
P95_LIMIT_MS = float(os.getenv("PERF_P95_MS", "2000"))


def test_create_quote_under_parallel_load(client, wallets, extras):
    """TC-13: 20 parallel quote requests all succeed, stay correct and stay within the latency budget."""
    health_before = get_health()
    # 0.01 keeps the fee at 0.000001: a smaller amount gets an exponent-notation fee the models reject (BUG-8)
    body = quote_body(wallets["ETH"], wallets["USDT"], "0.01")

    def create(_):
        start = time.perf_counter()
        response = client.create_quote_raw(body)
        return response, (time.perf_counter() - start) * 1000

    with ThreadPoolExecutor(PARALLEL_REQUESTS) as pool:
        results = list(pool.map(create, range(PARALLEL_REQUESTS)))

    latencies = sorted(ms for _, ms in results)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    stats = (
        f"{PARALLEL_REQUESTS} parallel POST /api/v1/quote: min {latencies[0]:.0f}ms, "
        f"avg {statistics.mean(latencies):.0f}ms, p95 {p95:.0f}ms, max {latencies[-1]:.0f}ms (limit p95 {P95_LIMIT_MS:.0f}ms)"
    )
    extras.append(pytest_html.extras.text(stats, name="latency"))
    print(stats)

    statuses = [r.status_code for r, _ in results]
    assert statuses == [201] * PARALLEL_REQUESTS, f"statuses under load: {statuses}"
    quotes = [Quote.model_validate_json(r.content) for r, _ in results]
    assert len({q.uuid for q in quotes}) == PARALLEL_REQUESTS, "duplicate quote uuids"
    assert all(q.fee == q.amount_in * FEE_RATE for q in quotes), "wrong fee under load"
    assert p95 <= P95_LIMIT_MS, stats

    # /health counts authenticated requests across all users, so it must have grown by at least our load
    health_after = get_health()
    assert health_after.total_authenticated_requests >= health_before.total_authenticated_requests + PARALLEL_REQUESTS
