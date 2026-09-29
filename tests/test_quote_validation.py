import re
from decimal import Decimal

import pytest

from api.client import quote_body
from api.models import DECIMAL_PATTERN, ErrorResponse, Quote
from tests.helpers import balances

pytestmark = pytest.mark.negative


def smallest_unit(wallet) -> Decimal:
    return Decimal(1).scaleb(-wallet.currency.quantity_precision)


# Source of each expected result. The spec only types the amounts (a signed decimal, no minimum) and documents
# 201/422, so most of these rules are not in it:
#   Spec       - the OpenAPI spec says so
#   Domain     - follows from the task brief (trades come out of the wallet balance)
#   Assumption - not in the spec or the brief; what a trading API should do, to confirm with the product owner
@pytest.mark.parametrize(
    "amount, expected_status",
    [
        # Assumption: nothing to trade, so it is rejected. The 400 is what the server returns (not in the spec).
        # BUG-3: the rejection comes with the misleading "one of amountIn/amountOut" message
        pytest.param(lambda eth: {"amount_in": Decimal("0")}, 400, id="zero"),
        # Assumption: a negative amount is rejected (the spec's pattern allows a sign)
        pytest.param(
            lambda eth: {"amount_in": Decimal("-1")}, 400, id="negative",
            marks=pytest.mark.xfail(reason="BUG-1: negative amounts are accepted (201): amountIn = -1", strict=True),
        ),
        # Assumption: the same rule for the other amount field
        pytest.param(
            lambda eth: {"amount_out": Decimal("-1")}, 400, id="negative-amount-out",
            marks=pytest.mark.xfail(reason="BUG-1: negative amounts are accepted (201): amountOut = -1", strict=True),
        ),
        # Assumption: more decimals than the currency's quantityPrecision is rejected, not silently rounded
        pytest.param(
            lambda eth: {"amount_in": smallest_unit(eth) / 10}, 400, id="below-precision",
            marks=pytest.mark.xfail(
                reason="BUG-4: sub-precision amountIn is accepted, rounded to 0, but amountOut is still > 0",
                strict=True,
            ),
        ),
        # Spec: fee fields are decimal strings matching DECIMAL_PATTERN, which has no exponent
        pytest.param(
            lambda eth: {"amount_in": Decimal("0.001")}, 201, id="small-amount-fee-notation",
            marks=pytest.mark.xfail(
                reason="BUG-8: the 1E-7 fee is sent in exponent notation, which the spec's decimal pattern forbids",
                strict=True,
            ),
        ),
        # Domain: the whole available balance can be traded
        pytest.param(lambda eth: {"amount_in": eth.available}, 201, id="exactly-available"),
        # Domain: one unit more than available is rejected. The 412 and its message are what the server returns
        # (not in the spec)
        pytest.param(lambda eth: {"amount_in": eth.available + smallest_unit(eth)}, 412, id="available-plus-one-unit"),
    ],
)
def test_amount_boundaries(client, wallets, amount, expected_status):
    """TC-08: amountIn / amountOut must be positive, within the currency precision and no more than the available
    balance."""
    eth = wallets["ETH"]
    amounts = amount(eth)
    quotes_before = len(client.list_quotes())

    response = client.create_quote_raw(quote_body(eth, wallets["USDT"], **amounts))

    if expected_status == 201:
        assert response.status_code == 201, response.text
        # the spec types the fee fields as decimal strings, so exponent notation ("1E-7") is a violation
        body = response.json()
        fees = {"fee": body["fee"], "fees.value.service": body["fees"]["value"]["service"]}
        for name, value in fees.items():
            assert re.fullmatch(DECIMAL_PATTERN, value), f"{name} {value!r} doesn't match the spec's decimal pattern"
        quote = Quote.model_validate_json(response.content)
        assert quote.amount_in == amounts["amount_in"]
        assert quote.amount_out > 0
        assert len(client.list_quotes()) == quotes_before + 1
    else:
        if expected_status == 412:
            assert response.status_code == 412, response.text
            error = ErrorResponse.model_validate_json(response.content)
            assert error.detail == f"Insufficient funds available in source wallet #{eth.id}."
        else:
            assert response.status_code in (400, 422), response.text
        assert len(client.list_quotes()) == quotes_before
    # creating a quote never moves money
    assert balances(client.get_wallets()) == balances(wallets)
