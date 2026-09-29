import uuid
from datetime import datetime, timezone

import httpx
import pytest

from api.client import BASE_URL, get_health, quote_body
from api.models import ErrorResponse, HTTPValidationError, QuoteStatus
from tests.helpers import balances

pytestmark = pytest.mark.security


def test_account_cannot_access_another_accounts_resources(client, wallets, second_client):
    """TC-05: account B must not see or use account A's quotes and wallets, and must not be able to tell they exist."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], "0.01")
    b_wallets = second_client.get_wallets()

    # baseline: what B gets for resources that don't exist at all
    missing_quote = second_client.get_quote_raw(uuid.uuid4())
    missing_accept = second_client.accept_quote_raw(uuid.uuid4())
    missing_wallet = second_client.get_wallet_raw(999999999)
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
    body = quote_body(wallets["ETH"], b_wallets["USDT"], "0.01")
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
    """TC-11: protected endpoints reject missing or bad tokens. A valid token works, as /echo confirms."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], "0.01")
    protected = [
        ("GET", "/api/wallet", None),
        ("POST", "/echo", {"probe": 1}),
        ("POST", "/api/v1/quote", quote_body(wallets["ETH"], wallets["USDT"], "0.01")),
        ("PUT", f"/api/v1/quote/accept/{quote.uuid}", None),
    ]
    for case, headers in INVALID_AUTH.items():
        for method, path, body in protected:
            response = httpx.request(method, f"{BASE_URL}{path}", headers=headers, json=body)
            assert response.status_code == 401, f"{case} {method} {path}: {response.status_code} {response.text}"
            assert ErrorResponse.model_validate_json(response.content).detail in ("Not authenticated", "Unauthorized")

    # the rejected calls had no effect: no extra quote, the existing one was not accepted, no money moved
    assert [q.uuid for q in client.list_quotes()] == [quote.uuid]
    assert client.get_quote(quote.uuid).quote_status == QuoteStatus.PENDING
    assert balances(client.get_wallets()) == balances(wallets)

    # valid token: /echo returns the token expiry and echoes the body back unchanged
    payload = {"text": "hello", "number": 42, "nested": {"list": [1, 2, 3]}}
    echo = client.echo(payload)
    assert echo.request_payload == payload

    # expiry is reported without a timezone; it matches the /init expiry as UTC, about 24h ahead
    expiry = datetime.strptime(echo.auth_token_expiry_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert abs(expiry.timestamp() - account.expiry) <= 2
    assert 23.9 * 3600 < account.expiry - datetime.now(timezone.utc).timestamp() <= 24 * 3600

    # a body that isn't JSON gets the spec's 422 validation error
    response = client.request("POST", "/echo", content=b"not json{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    validation_error = HTTPValidationError.model_validate_json(response.content)
    assert any("body" in e.loc for e in validation_error.detail), validation_error.detail

    # /health is the one endpoint that needs no token: get_health() sends none and validates the metrics schema.
    # The counter is global and this account has just made authenticated calls, so it can't be 0
    assert get_health().total_authenticated_requests > 0
