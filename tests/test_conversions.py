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
    # 1. create quote
    quote = client.create_quote(wallets[source], wallets[target], amount)

    assert quote.from_ == source
    assert quote.to == target
    assert quote.amount_in == amount
    assert quote.quote_status == QuoteStatus.PENDING
    assert quote.payment_status == PaymentStatus.PENDING
    assert quote.acceptance_expiry_date - quote.date_created == 20

    # fee and amountOut = (amountIn - fee) * price
    expected_fee = amount * FEE_RATE
    assert quote.fee == expected_fee
    assert quote.fees.percentage.service == Decimal("0.01")
    expected_out = (amount - expected_fee) * quote.price
    # the returned price is rounded to 8 decimals (TRX->ETH is ~0.00008), so allow 0.01% difference
    assert abs(quote.amount_out - expected_out) <= expected_out * Decimal("0.0001")

    # 2. accept - terms must stay the same
    accepted = client.accept_quote(quote.uuid)

    assert accepted.quote_status == QuoteStatus.ACCEPTED
    assert accepted.payment_status == PaymentStatus.PROCESSING
    assert accepted.price == quote.price
    assert accepted.amount_out == quote.amount_out

    # 3. wait for settlement
    settled = client.wait_for_settlement(quote.uuid)
    assert settled.quote_status == QuoteStatus.PAYMENT_OUT_PROCESSED

    # 4. balances
    after = client.get_wallets()
    assert after[source].balance == wallets[source].balance - quote.amount_in
    assert after[target].balance == wallets[target].balance + quote.amount_out
