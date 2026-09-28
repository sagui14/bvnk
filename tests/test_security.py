import uuid
from datetime import datetime, timezone

import httpx
import pytest

from api.client import BASE_URL, quote_body
from api.models import QuoteStatus
from tests.helpers import assert_balances_unchanged, assert_error, assert_validation_error

pytestmark = pytest.mark.security


def test_account_cannot_access_another_accounts_resources(client, wallets, second_client):
    """TC-05: account B must not see or use account A's quotes and wallets, and must not be able to tell they exist."""
    quote = client.create_quote(wallets["ETH"], wallets["USDT"], "0.01")
    b_wallets = second_client.get_wallets()

    # baseline: what B gets for resources that don't exist at all
    missing_quote = second_client.request("GET", f"/api/v1/quote/{uuid.uuid4()}")
    missing_accept = second_client.request("PUT", f"/api/v1/quote/accept/{uuid.uuid4()}")
    missing_wallet = second_client.request("GET", "/api/wallet/999999999")
    assert_error(missing_quote, 404)
    assert_error(missing_accept, 404)
    assert_error(missing_wallet, 404)

    # A's resources must look exactly like missing ones
    get_foreign = second_client.request("GET", f"/api/v1/quote/{quote.uuid}")
    accept_foreign = second_client.request("PUT", f"/api/v1/quote/accept/{quote.uuid}")
    wallet_foreign = second_client.request("GET", f"/api/wallet/{wallets['ETH'].id}")
    assert (get_foreign.status_code, get_foreign.json()) == (missing_quote.status_code, missing_quote.json())
    assert (accept_foreign.status_code, accept_foreign.json()) == (missing_accept.status_code, missing_accept.json())
    assert (wallet_foreign.status_code, wallet_foreign.json()) == (missing_wallet.status_code, missing_wallet.json())

    # A's quote is not in B's list
    assert quote.uuid not in {q.uuid for q in second_client.list_quotes()}

    # B can't trade out of A's wallet
    body = quote_body(wallets["ETH"], b_wallets["USDT"], "0.01")
    response = second_client.request("POST", "/api/v1/quote", json=body)
    assert_error(response, 400, f"Source wallet with ID #{wallets['ETH'].id} not found.")

    # malformed ids are rejected by validation before any lookup
    assert_validation_error(second_client.request("GET", "/api/v1/quote/not-a-uuid"), "quote_uuid")
    assert_validation_error(second_client.request("PUT", "/api/v1/quote/accept/not-a-uuid"), "quote_uuid")
    assert_validation_error(second_client.request("GET", "/api/wallet/abc"), "wallet_id")

    # nothing B did affected A
    assert client.get_quote(quote.uuid).quote_status == QuoteStatus.PENDING
    assert_balances_unchanged(client, wallets)
    assert_balances_unchanged(second_client, b_wallets)


INVALID_AUTH = {
    "no-header": {},
    "invalid-token": {"Authorization": "Bearer not-a-real-token"},
    "wrong-scheme": {"Authorization": "Token not-a-real-token"},
}


def test_authentication(account, client):
    """TC-11: protected endpoints reject missing or bad tokens. A valid token works, as /echo confirms."""
    for case, headers in INVALID_AUTH.items():
        for method, path in [("GET", "/api/wallet"), ("POST", "/echo")]:
            response = httpx.request(method, f"{BASE_URL}{path}", headers=headers, json={"probe": 1})
            assert response.status_code == 401, f"{case} {method} {path}: {response.status_code} {response.text}"
            assert assert_error(response, 401).detail in ("Not authenticated", "Unauthorized")

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
    assert_validation_error(response, "body")
