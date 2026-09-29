import time
from decimal import Decimal

import pytest

from api.models import ErrorResponse, PaymentStatus, Quote, QuoteStatus
from tests.helpers import balances


def test_quote_cannot_be_accepted_twice(client, wallets):
    """TC-06: a second accept of the same quote is rejected and the trade is only executed once."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], amount_in="0.01")
    client.accept_quote(quote.uuid)

    second = client.accept_quote_raw(quote.uuid)
    assert second.status_code == 400, second.text
    assert ErrorResponse.model_validate_json(second.content).detail == "Bad Request"

    client.wait_for_settlement(quote.uuid)
    after = client.get_wallets()
    assert after["ETH"].balance == wallets["ETH"].balance - quote.amount_in
    assert after["USDT"].balance == wallets["USDT"].balance + quote.amount_out


@pytest.mark.slow
@pytest.mark.parametrize(
    "seconds, expected_status",
    [
        pytest.param(19, 200, id="19s-before-expiry"),
        pytest.param(21, 412, id="21s-after-expiry"),
    ],
)
def test_quote_expiry_boundary(client, wallets, seconds, expected_status):
    """TC-07: a quote can be accepted within 20 seconds of creation and not after.

    The outcome is all or nothing: accepted and settled, or expired with no money moved.
    """
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], amount_in="0.01")
    # measured on the local monotonic clock from the create response (the latest the server can have created
    # the quote): server timestamps are whole seconds and the local wall clock can be skewed from the server's
    created = time.monotonic()
    assert quote.acceptance_expiry_date - quote.date_created == 20

    time.sleep(max(0.0, created + seconds - time.monotonic()))
    sent = time.monotonic() - created
    response = client.accept_quote_raw(quote.uuid)
    assert response.status_code == expected_status, (
        f"accept sent {sent:.2f}s after create (took {time.monotonic() - created - sent:.2f}s): "
        f"{response.status_code} {response.text}"
    )

    if expected_status == 200:
        accepted = Quote.model_validate_json(response.content)
        assert accepted.quote_status == QuoteStatus.ACCEPTED
        # settlement finishes after the 20s mark, so this also checks expiry doesn't overwrite an accepted quote
        settled = client.wait_for_settlement(quote.uuid)
        assert settled.quote_status == QuoteStatus.PAYMENT_OUT_PROCESSED
        after = client.get_wallets()
        assert after["ETH"].balance == wallets["ETH"].balance - quote.amount_in
        assert after["USDT"].balance == wallets["USDT"].balance + quote.amount_out
    else:
        assert ErrorResponse.model_validate_json(response.content).detail
        expired = client.get_quote(quote.uuid)
        assert expired.quote_status == QuoteStatus.EXPIRED
        assert expired.payment_status == PaymentStatus.EXPIRED
        assert balances(client.get_wallets()) == balances(wallets)


@pytest.mark.xfail(
    reason="BUG-9: accept doesn't check funds, so the second quote is accepted (200) and ETH ends negative",
    strict=True,
)
def test_accept_is_rejected_when_funds_are_no_longer_available(client, wallets):
    """TC-10: a quote must be covered by the funds available when it is accepted, not only when it was created.

    Two quotes for 60% of the balance are each affordable, so both get created. Once the first has settled the wallet
    can no longer cover the second, so accepting it has to be refused and no wallet may end up negative.
    The spec doesn't document a rejection on accept, so any 4xx is accepted here.
    """
    eth, usdt = wallets["ETH"], wallets["USDT"]
    amount = (eth.available * Decimal("0.6")).quantize(Decimal(1).scaleb(-eth.currency.quantity_precision))
    first = client.create_quote(eth, usdt, amount_in=amount)
    second = client.create_quote(eth, usdt, amount_in=amount)

    client.accept_quote(first.uuid)
    client.wait_for_settlement(first.uuid)
    after_first = client.get_wallets()
    assert after_first["ETH"].available < second.amount_in, (
        "precondition: the wallet can no longer cover the second quote"
    )

    response = client.accept_quote_raw(second.uuid)

    assert 400 <= response.status_code < 500, f"accept was not refused: {response.status_code} {response.text}"
    assert client.get_quote(second.uuid).quote_status != QuoteStatus.ACCEPTED
    final = client.get_wallets()
    assert final["ETH"].balance >= 0
    assert balances(final) == balances(after_first)
