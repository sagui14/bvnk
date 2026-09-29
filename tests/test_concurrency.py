import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from api.models import PaymentStatus

pytestmark = pytest.mark.concurrency


def accept_in_parallel(client, uuids: list) -> list:
    """Fire all accept requests at the same moment and return the raw responses."""
    barrier = threading.Barrier(len(uuids))

    def accept(uuid):
        barrier.wait()
        return client.accept_quote_raw(uuid)

    with ThreadPoolExecutor(len(uuids)) as pool:
        return list(pool.map(accept, uuids))


@pytest.mark.parametrize(
    "quotes, share, accepts_per_quote, expected_accepted",
    [
        # the same quote accepted 5 times at once. The spec does not say whether a repeat accept is rejected or
        # returns 200 again, so the accept count is not asserted (None); the quote must still settle exactly once
        pytest.param(1, Decimal("0.1"), 5, None, id="same-quote-5x"),
        # two quotes for 60% of the balance each: together they exceed it. The spec does not say whether the
        # second accept must be rejected, so the accept count is not asserted (None); whichever way the server
        # goes, the ledger must still match the quotes that report SUCCESS and the balance must not go negative
        pytest.param(
            2, Decimal("0.6"), 1, None, id="overcommit-2x60pct",
            marks=pytest.mark.xfail(
                reason=(
                    "BUG-5: overlapping settlements overwrite each other, so only one debit/credit is applied: "
                    "quotes that together exceed the balance (this also hides BUG-9)"
                ),
                strict=True,
            ),
        ),
        # two quotes for 10% each: both are affordable, so both must be applied in full
        pytest.param(
            2, Decimal("0.1"), 1, 2, id="within-funds-2x10pct",
            marks=pytest.mark.xfail(
                reason=(
                    "BUG-5: overlapping settlements overwrite each other, so only one debit/credit is applied: "
                    "two affordable quotes"
                ),
                strict=True,
            ),
        ),
    ],
)
def test_concurrent_accepts_keep_ledger_consistent(
    client, wallets, quotes, share, accepts_per_quote, expected_accepted
):
    """TC-04: parallel accepts never double spend, and balances always equal the sum of the settled quotes."""
    eth, usdt = wallets["ETH"], wallets["USDT"]
    amount = (eth.available * share).quantize(Decimal(1).scaleb(-eth.currency.quantity_precision))
    created = [client.create_quote(eth, usdt, amount_in=amount) for _ in range(quotes)]

    uuids = [q.uuid for q in created for _ in range(accepts_per_quote)]
    responses = accept_in_parallel(client, uuids)

    statuses = [r.status_code for r in responses]
    assert all(s < 500 for s in statuses), f"server error under concurrency: {statuses}"
    assert 200 in statuses, f"no accept succeeded: {statuses}"
    if expected_accepted is not None:
        assert statuses.count(200) == expected_accepted, f"accept results: {statuses}"
    assert all(s in (200, 400, 412) for s in statuses), f"unexpected status: {statuses}"
    accepted = {uuid for uuid, r in zip(uuids, responses, strict=True) if r.status_code == 200}

    # wait for every accepted quote, then check the ledger against the quotes that report SUCCESS
    for quote in created:
        if client.get_quote(quote.uuid).payment_status == PaymentStatus.PROCESSING:
            client.wait_for_settlement(quote.uuid)
    settled = [q for q in client.list_quotes() if q.payment_status == PaymentStatus.SUCCESS]
    assert {q.uuid for q in settled} == accepted, f"exactly the accepted quotes must settle: {statuses}"

    after = client.get_wallets()
    assert after["ETH"].balance >= 0
    assert after["ETH"].balance == eth.balance - sum(q.amount_in for q in settled)
    assert after["USDT"].balance == usdt.balance + sum(q.amount_out for q in settled)
