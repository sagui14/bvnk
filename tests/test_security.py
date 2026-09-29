import uuid
from datetime import UTC, datetime

import httpx
import pytest

from api.client import BASE_URL, get_health, quote_body
from api.models import ErrorResponse, HTTPValidationError, QuoteStatus
from tests.helpers import balances

pytestmark = pytest.mark.security

# far above the sequential wallet ids the simulator hands out, so it belongs to no account
NONEXISTENT_WALLET_ID = 999_999_999


def test_account_cannot_access_another_accounts_resources(client, wallets, second_client):
    """TC-05: account B must not see or use account A's quotes and wallets, and must not be able to tell they exist."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], amount_in="0.01")
    b_wallets = second_client.get_wallets()

    # the owner can read its own wallet, and it matches the entry in the wallet list
    assert client.get_wallet(wallets["ETH"].id) == wallets["ETH"]

    # baseline: what B gets for resources that don't exist at all
    missing_quote = second_client.get_quote_raw(uuid.uuid4())
    missing_accept = second_client.accept_quote_raw(uuid.uuid4())
    missing_wallet = second_client.get_wallet_raw(NONEXISTENT_WALLET_ID)
    for missing in (missing_quote, missing_accept, missing_wallet):
        assert missing.status_code == 404, missing.text
        assert ErrorResponse.model_validate_json(missing.content).detail == "Not Found"

    # A's resources must look exactly like missing ones
    get_foreign = second_client.get_quote_raw(quote.uuid)
    accept_foreign = second_client.accept_quote_raw(quote.uuid)
    wallet_foreign = second_client.get_wallet_raw(wallets["ETH"].id)
    assert (get_foreign.status_code, get_foreign.json()) == (missing_quote.status_code, missing_quote.json())
    assert (accept_foreign.status_code, accept_foreign.json()) == (missing_accept.status_code, missing_accept.json())
    assert (wallet_foreign.status_code, wallet_foreign.json()) == (missing_wallet.status_code, missing_wallet.json())

    # A's quote is not in B's list
    assert quote.uuid not in {q.uuid for q in second_client.list_quotes()}

    # B can't trade out of A's wallet
    body = quote_body(wallets["ETH"], b_wallets["USDT"], amount_in="0.01")
    response = second_client.create_quote_raw(body)
    assert response.status_code == 400, response.text
    error = ErrorResponse.model_validate_json(response.content)
    assert error.detail == f"Source wallet with ID #{wallets['ETH'].id} not found."

    # malformed ids are rejected by validation before any lookup
    malformed = [
        (second_client.get_quote_raw("not-a-uuid"), "quote_uuid"),
        (second_client.accept_quote_raw("not-a-uuid"), "quote_uuid"),
        (second_client.get_wallet_raw("abc"), "wallet_id"),
    ]
    for response, field in malformed:
        assert response.status_code == 422, response.text
        validation_error = HTTPValidationError.model_validate_json(response.content)
        assert any(field in e.loc for e in validation_error.detail), validation_error.detail

    # nothing B did affected A
    assert client.get_quote(quote.uuid).quote_status == QuoteStatus.PENDING
    assert balances(client.get_wallets()) == balances(wallets)
    assert balances(second_client.get_wallets()) == balances(b_wallets)


INVALID_AUTH = {
    "no-header": {},
    "invalid-token": {"Authorization": "Bearer not-a-real-token"},
    "wrong-scheme": {"Authorization": "Token not-a-real-token"},
}


def test_authentication(account, client, wallets):
    """TC-09: protected endpoints reject missing or bad tokens. A valid token works, as /echo confirms."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], amount_in="0.01")
    protected = [
        ("GET", "/api/wallet", None),
        ("POST", "/echo", {"probe": 1}),
        ("POST", "/api/v1/quote", quote_body(wallets["ETH"], wallets["USDT"], amount_in="0.01")),
        ("PUT", f"/api/v1/quote/accept/{quote.uuid}", None),
    ]
    # one client with no token for all the rejected calls: same timeout as BvnkClient, and the connection is reused
    with httpx.Client(base_url=BASE_URL, timeout=30) as anonymous:
        for case, headers in INVALID_AUTH.items():
            for method, path, body in protected:
                response = anonymous.request(method, path, headers=headers, json=body)
                assert response.status_code == 401, f"{case} {method} {path}: {response.status_code} {response.text}"
                detail = ErrorResponse.model_validate_json(response.content).detail
                assert detail in ("Not authenticated", "Unauthorized")

    # the rejected calls had no effect: no extra quote, the existing one was not accepted, no money moved.
    # Under load the loop above can run past the 20s quote expiry, so a quote that was never accepted is PENDING or
    # EXPIRED
    assert [q.uuid for q in client.list_quotes()] == [quote.uuid]
    assert client.get_quote(quote.uuid).quote_status in (QuoteStatus.PENDING, QuoteStatus.EXPIRED)
    assert balances(client.get_wallets()) == balances(wallets)

    # valid token: /echo returns the token expiry and echoes the body back unchanged
    payload = {"text": "hello", "number": 42, "nested": {"list": [1, 2, 3]}}
    echo = client.echo(payload)
    assert echo.request_payload == payload

    # expiry is reported without a timezone; it matches the /init expiry as UTC, about 24h ahead
    expiry = datetime.strptime(echo.auth_token_expiry_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert abs(expiry.timestamp() - account.expiry) <= 2
    assert 23.9 * 3600 < account.expiry - datetime.now(UTC).timestamp() <= 24 * 3600

    # a body that isn't JSON gets the spec's 422 validation error
    response = client.request("POST", "/echo", content=b"not json{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    validation_error = HTTPValidationError.model_validate_json(response.content)
    assert any("body" in e.loc for e in validation_error.detail), validation_error.detail

    # /health is the one endpoint that needs no token: get_health() sends none and validates the metrics schema.
    # The counter is global and this account has just made authenticated calls, so it can't be 0.
    # BUG-10: /health sometimes returns 500 while the suite runs in parallel. Deliberately not retried, so it shows up
    assert get_health().total_authenticated_requests > 0
