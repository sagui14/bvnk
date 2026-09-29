from decimal import Decimal

import pytest

from api.models import PaymentStatus, QuoteStatus
from tests.helpers import FEE_RATE


@pytest.mark.parametrize(
    "source, target, amount",
    [
        ("ETH", "TRX", Decimal("1")),
        ("TRX", "USDT", Decimal("420")),
        ("TRX", "ETH", Decimal("987")),
    ],
)
def test_conversion(client, wallets, source, target, amount):
    """TC-01 to TC-03: a full conversion (create quote, accept, settle) charges the right fee and moves the right
    amounts between wallets."""
    # 1. create quote
    quote = client.create_quote(wallets[source], wallets[target], amount_in=amount)

    assert quote.from_ == source
    assert quote.to == target
    assert quote.amount_in == amount
    assert quote.quote_status == QuoteStatus.PENDING
    assert quote.payment_status == PaymentStatus.PENDING
    assert quote.acceptance_expiry_date - quote.date_created == 20

    # fee = amountIn * FEE_RATE, amountOut = (amountIn - fee) * price
    expected_fee = amount * FEE_RATE
    assert quote.fee == expected_fee
    assert quote.fees.percentage.service == Decimal("0.01")
    net = amount - expected_fee
    # amountOut should equal net * price, but only within rounding: the returned price is rounded to price_precision
    # (amountOut uses the unrounded rate) and amountOut is rounded to the target's quantity_precision.
    # Allowed gap = net * one price unit + one target unit
    price_unit = Decimal(1).scaleb(-wallets[source].currency.price_precision)
    target_unit = Decimal(1).scaleb(-wallets[target].currency.quantity_precision)
    assert abs(quote.amount_out - net * quote.price) <= net * price_unit + target_unit

    # 2. accept - terms must stay the same
    accepted = client.accept_quote(quote.uuid)

    assert accepted.quote_status == QuoteStatus.ACCEPTED
    assert accepted.payment_status == PaymentStatus.PROCESSING
    assert accepted.price == quote.price
    assert accepted.amount_out == quote.amount_out

    # 3. wait for settlement
    settled = client.wait_for_settlement(quote.uuid)
    assert settled.quote_status == QuoteStatus.PAYMENT_OUT_PROCESSED
    # rates fluctuate, but a quote's terms are locked: settlement uses the quoted price, amountOut and fee
    assert (settled.price, settled.amount_out, settled.fee) == (quote.price, quote.amount_out, quote.fee)

    # 4. balances
    after = client.get_wallets()
    assert after[source].balance == wallets[source].balance - quote.amount_in
    assert after[target].balance == wallets[target].balance + quote.amount_out
