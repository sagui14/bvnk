import re
from decimal import Decimal

import pytest

from api.client import quote_body
from api.models import DECIMAL_PATTERN, ErrorResponse, HTTPValidationError, Quote
from tests.helpers import balances

pytestmark = pytest.mark.negative

AMOUNT_ERROR = "One of 'amountIn' or 'amountOut' must be specified but not both."


def smallest_unit(wallet) -> Decimal:
    return Decimal(1).scaleb(-wallet.currency.quantity_precision)


@pytest.mark.parametrize(
    "amount, expected_status",
    [
        # BUG-3: 0 is rejected, but with the misleading "one of amountIn/amountOut" message
        pytest.param(lambda eth: {"amount_in": Decimal("0")}, 400, id="zero"),
        pytest.param(
            lambda eth: {"amount_in": Decimal("-1")}, 400, id="negative",
            marks=pytest.mark.xfail(reason="BUG-1: negative amountIn is accepted (201)", strict=True),
        ),
        pytest.param(
            lambda eth: {"amount_in": -smallest_unit(eth)}, 400, id="negative-smallest-unit",
            marks=pytest.mark.xfail(reason="BUG-1: negative amountIn is accepted (201)", strict=True),
        ),
        pytest.param(
            lambda eth: {"amount_out": Decimal("-1")}, 400, id="negative-amount-out",
            marks=pytest.mark.xfail(reason="BUG-1: negative amounts are accepted (201), amountOut mode included", strict=True),
        ),
        pytest.param(
            lambda eth: {"amount_in": smallest_unit(eth) / 10}, 400, id="below-precision",
            marks=pytest.mark.xfail(reason="BUG-4: sub-precision amountIn is accepted and rounded to a 0 quote", strict=True),
        ),
        pytest.param(
            lambda eth: {"amount_in": Decimal("0.001")}, 201, id="small-amount-fee-notation",
            marks=pytest.mark.xfail(
                reason="BUG-8: the 1E-7 fee is sent in exponent notation, which the spec's decimal pattern forbids",
                strict=True,
            ),
        ),
        pytest.param(lambda eth: {"amount_in": eth.available}, 201, id="exactly-available"),
        pytest.param(lambda eth: {"amount_in": eth.available + smallest_unit(eth)}, 412, id="available-plus-one-unit"),
    ],
)
def test_amount_boundaries(client, wallets, amount, expected_status):
    """TC-08: amountIn / amountOut must be positive, within the currency precision and no more than the available balance."""
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

    response = client.create_quote_raw(body)

    assert response.status_code == expected_status, response.text
    if expected_status == 422:
        # `expected` is the field the validation error must point at
        validation_error = HTTPValidationError.model_validate_json(response.content)
        assert any(expected in e.loc for e in validation_error.detail), validation_error.detail
    else:
        error = ErrorResponse.model_validate_json(response.content)
        if expected is not None:
            assert error.detail == expected
    assert len(client.list_quotes()) == quotes_before
    assert balances(client.get_wallets()) == balances(wallets)
