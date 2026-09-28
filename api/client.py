import os
import time
from decimal import Decimal

import httpx
from dotenv import load_dotenv

from api.models import (
    EchoResponse,
    HealthMetrics,
    InitResponse,
    PaymentStatus,
    Quote,
    QuoteRequest,
    Wallet,
)

load_dotenv()
BASE_URL = os.getenv("BASE_URL", "https://qa-simulator.test.bvnk.com")


def init_account() -> InitResponse:
    """/init creates a new account with default balances and returns a token for it."""
    response = httpx.get(f"{BASE_URL}/init", timeout=30)
    assert response.status_code == 200, response.text
    return InitResponse.model_validate_json(response.content)


def get_health() -> HealthMetrics:
    """/health is not protected (no bearer token in the spec)."""
    response = httpx.get(f"{BASE_URL}/health", timeout=30)
    assert response.status_code == 200, response.text
    return HealthMetrics.model_validate_json(response.content)


class BvnkClient:
    """One method per endpoint. Each checks the success status code and validates the body against its schema.

    For negative tests use `request()`, which returns the raw response without any checks.
    """

    def __init__(self, token: str):
        self.http = httpx.Client(
            base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self.http.request(method, path, **kwargs)

    def echo(self, body) -> EchoResponse:
        response = self.http.post("/echo", json=body)
        assert response.status_code == 200, response.text
        return EchoResponse.model_validate_json(response.content)

    def get_wallets(self) -> dict[str, Wallet]:
        response = self.http.get("/api/wallet")
        assert response.status_code == 200, response.text
        wallets = [Wallet.model_validate(w) for w in response.json()]
        return {w.currency.code: w for w in wallets}

    def get_wallet(self, wallet_id: int) -> Wallet:
        response = self.http.get(f"/api/wallet/{wallet_id}")
        assert response.status_code == 200, response.text
        return Wallet.model_validate_json(response.content)

    def create_quote(self, from_wallet: Wallet, to_wallet: Wallet, amount_in=None, amount_out=None) -> Quote:
        body = quote_request(from_wallet, to_wallet, amount_in, amount_out).to_body()
        response = self.http.post("/api/v1/quote", json=body)
        assert response.status_code == 201, response.text
        return Quote.model_validate_json(response.content)

    def list_quotes(self) -> list[Quote]:
        response = self.http.get("/api/v1/quote")
        assert response.status_code == 200, response.text
        return [Quote.model_validate(q) for q in response.json()]

    def get_quote(self, uuid) -> Quote:
        response = self.http.get(f"/api/v1/quote/{uuid}")
        assert response.status_code == 200, response.text
        return Quote.model_validate_json(response.content)

    def accept_quote(self, uuid) -> Quote:
        response = self.http.put(f"/api/v1/quote/accept/{uuid}")
        assert response.status_code == 200, response.text
        return Quote.model_validate_json(response.content)

    def wait_for_settlement(self, uuid, timeout: float = 15) -> Quote:
        # accept returns straight away with PROCESSING, the trade settles a few seconds later
        deadline = time.time() + timeout
        while True:
            quote = self.get_quote(uuid)
            if quote.payment_status == PaymentStatus.SUCCESS:
                return quote
            if time.time() > deadline:
                raise AssertionError(f"quote {uuid} not settled after {timeout}s, status {quote.payment_status}")
            time.sleep(0.5)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def quote_request(from_wallet: Wallet, to_wallet: Wallet, amount_in=None, amount_out=None) -> QuoteRequest:
    return QuoteRequest(
        from_=from_wallet.currency.code,
        to=to_wallet.currency.code,
        from_wallet=from_wallet.id,
        to_wallet=to_wallet.id,
        amount_in=None if amount_in is None else Decimal(str(amount_in)),
        amount_out=None if amount_out is None else Decimal(str(amount_out)),
    )


def quote_body(from_wallet: Wallet, to_wallet: Wallet, amount_in=None, amount_out=None) -> dict:
    """Raw JSON body for negative tests that need to break it."""
    return quote_request(from_wallet, to_wallet, amount_in, amount_out).to_body()
