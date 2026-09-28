from decimal import Decimal

import pytest

from api.client import quote_body
from api.models import Quote
from tests.helpers import assert_balances_unchanged, assert_error, assert_validation_error

pytestmark = pytest.mark.negative

AMOUNT_ERROR = "One of 'amountIn' or 'amountOut' must be specified but not both."


def smallest_unit(wallet) -> Decimal:
    return Decimal(1).scaleb(-wallet.currency.quantity_precision)


@pytest.mark.parametrize(
    "amount, expected_status",
    [
        # BUG-3: 0 is rejected, but with the misleading "one of amountIn/amountOut" message
        pytest.param(lambda eth: Decimal("0"), 400, id="zero"),
        pytest.param(
            lambda eth: Decimal("-1"), 400, id="negative",
            marks=pytest.mark.xfail(reason="BUG-1: negative amountIn is accepted (201)", strict=True),
        ),
        pytest.param(
            lambda eth: smallest_unit(eth) / 10, 400, id="below-precision",
            marks=pytest.mark.xfail(reason="BUG-4: sub-precision amountIn is accepted and rounded to a 0 quote", strict=True),
        ),
        pytest.param(lambda eth: eth.available, 201, id="exactly-available"),
        pytest.param(lambda eth: eth.available + smallest_unit(eth), 412, id="available-plus-one-unit"),
    ],
)
def test_amount_boundaries(client, wallets, amount, expected_status):
    """TC-08: amountIn must be positive, within the currency precision and no more than the available balance."""
    eth = wallets["ETH"]
    amount_in = amount(eth)
    quotes_before = len(client.list_quotes())

    response = client.request("POST", "/api/v1/quote", json=quote_body(eth, wallets["USDT"], amount_in))

    if expected_status == 201:
        assert response.status_code == 201, response.text
        quote = Quote.model_validate_json(response.content)
        assert quote.amount_in == amount_in
        assert quote.amount_out > 0
        assert len(client.list_quotes()) == quotes_before + 1
    else:
        if expected_status == 412:
            assert_error(response, 412, f"Insufficient funds available in source wallet #{eth.id}.")
        else:
            assert response.status_code in (400, 422), response.text
        assert len(client.list_quotes()) == quotes_before
    # creating a quote never moves money
    assert_balances_unchanged(client, wallets)


def both_amounts(body, w):
    body["amountOut"] = "1"


def no_amount(body, w):
    del body["amountIn"]


def unsupported_currency(body, w):
    body["to"] = "XYZ"


def wallet_currency_mismatch(body, w):
    body["fromWallet"] = w["TRX"].id


def same_currency(body, w):
    body["to"], body["toWallet"] = "ETH", w["ETH"].id


def missing_from(body, w):
    del body["from"]


def non_numeric_amount(body, w):
    body["amountIn"] = "abc"


@pytest.mark.parametrize(
    "mutate, expected_status, expected",
    [
        pytest.param(both_amounts, 400, AMOUNT_ERROR, id="both-amounts"),
        pytest.param(no_amount, 400, AMOUNT_ERROR, id="no-amount"),
        pytest.param(
            unsupported_currency, 400, "Request to trade ETH for XYZ but destination wallet has currency USDT.",
            id="unsupported-currency",
        ),
        pytest.param(
            wallet_currency_mismatch, 400, "Request to trade ETH for USDT but source wallet has currency TRX.",
            id="wallet-currency-mismatch",
        ),
        pytest.param(
            same_currency, 400, None, id="same-currency",
            marks=pytest.mark.xfail(reason="BUG-2: ETH -> ETH is accepted, with a price != 1 and a fee", strict=True),
        ),
        pytest.param(missing_from, 422, "from", id="missing-required-field"),
        pytest.param(non_numeric_amount, 422, "amountIn", id="non-numeric-amount"),
    ],
)
def test_invalid_quote_payload(client, wallets, mutate, expected_status, expected):
    """TC-10: invalid quote requests are rejected with a clear error, and nothing is persisted."""
    body = quote_body(wallets["ETH"], wallets["USDT"], "0.01")
    mutate(body, wallets)
    quotes_before = len(client.list_quotes())

    response = client.request("POST", "/api/v1/quote", json=body)

    if expected_status == 422:
        # `expected` is the field the validation error must point at
        assert_validation_error(response, expected)
    else:
        assert_error(response, expected_status, expected)
    assert len(client.list_quotes()) == quotes_before
    assert_balances_unchanged(client, wallets)
