import time
from decimal import Decimal

import pytest

from api.models import ErrorResponse, PaymentStatus, Quote, QuoteStatus
from tests.helpers import FEE_RATE, balances


def test_quote_cannot_be_accepted_twice(client, wallets):
    """TC-06: a second accept of the same quote is rejected and the trade is only executed once."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], "0.01")
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
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], "0.01")
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


def test_quote_by_amount_out(client, wallets):
    """TC-09: quoting by the amount to receive. The target is credited exactly amountOut and the source pays amountIn + fee."""
    trx, usdt = wallets["TRX"], wallets["USDT"]
    wanted = Decimal("10")
    source_unit = Decimal(1).scaleb(-trx.currency.quantity_precision)
    price_unit = Decimal(1).scaleb(-trx.currency.price_precision)

    quote = client.create_quote(trx, usdt, amount_out=wanted)

    assert quote.from_ == "TRX" and quote.to == "USDT"
    assert quote.amount_out == wanted
    assert quote.quote_status == QuoteStatus.PENDING
    # the fee is still 0.01% of the source amount, to within one unit of the source currency (amountIn is rounded)
    assert quote.fees.percentage.service == Decimal("0.01")
    assert abs(quote.fee - quote.amount_in * FEE_RATE) <= source_unit
    # in this mode price is TRX per USDT (the inverse of amountIn mode, see README). It is rounded to price_precision
    # decimals, so amountIn - fee differs from amountOut * price by at most that rounding plus one source unit
    assert abs((quote.amount_in - quote.fee) - wanted * quote.price) <= wanted * price_unit + source_unit

    client.accept_quote(quote.uuid)
    client.wait_for_settlement(quote.uuid)

    after = client.get_wallets()
    assert after["USDT"].balance == usdt.balance + wanted
    assert after["TRX"].balance == trx.balance - quote.amount_in


def test_settlement_lifecycle_and_read_consistency(client, wallets):
    """TC-12: states move PENDING -> PROCESSING -> SUCCESS, and every read endpoint agrees on the result."""
    eth, usdt = wallets["ETH"], wallets["USDT"]
    quote = client.create_quote(eth, usdt, "0.5")
    assert quote.acceptance_date is None

    accepted = client.accept_quote(quote.uuid)
    assert accepted.quote_status == QuoteStatus.ACCEPTED
    assert accepted.payment_status == PaymentStatus.PROCESSING
    assert quote.date_created <= accepted.acceptance_date <= quote.acceptance_expiry_date

    # settlement is asynchronous: while the quote is still PROCESSING no balance has moved yet
    during = client.get_wallets()
    if client.get_quote(quote.uuid).payment_status == PaymentStatus.PROCESSING:
        assert during["ETH"].balance == eth.balance
        assert during["USDT"].balance == usdt.balance

    settled = client.wait_for_settlement(quote.uuid)
    assert settled.quote_status == QuoteStatus.PAYMENT_OUT_PROCESSED
    # BUG-7 (minor): acceptanceDate is overwritten with the settlement time, so only ordering is checked
    assert settled.acceptance_date >= accepted.acceptance_date
    assert settled.last_updated >= settled.date_created
    # the terms don't change on settlement
    assert (settled.amount_in, settled.amount_out, settled.price, settled.fee) == (
        quote.amount_in, quote.amount_out, quote.price, quote.fee,
    )

    after = client.get_wallets()
    assert after["ETH"].balance == eth.balance - quote.amount_in
    assert after["USDT"].balance == usdt.balance + quote.amount_out
    for code in ("ETH", "USDT"):
        # nothing is left reserved, and the single wallet endpoint agrees with the list
        assert after[code].balance == after[code].available
        assert client.get_wallet(after[code].id) == after[code]

    listed = {q.uuid: q for q in client.list_quotes()}
    assert listed[quote.uuid] == settled
