"""Meta check, not an API test: every endpoint and schema in /openapi.json must be covered by this project.

When the API adds an endpoint or a schema, this fails until a client method or model is added for it.
"""

from typing import Any

import httpx
import pytest
from pydantic import BeforeValidator

from api import client, models
from api.client import BASE_URL

# (method, path) from the spec -> the function in api/client.py that calls it
ENDPOINTS = {
    ("GET", "/init"): client.init_account,
    ("GET", "/health"): client.get_health,
    ("POST", "/echo"): client.BvnkClient.echo,
    ("GET", "/api/wallet"): client.BvnkClient.get_wallets,
    ("GET", "/api/wallet/{wallet_id}"): client.BvnkClient.get_wallet,
    ("GET", "/api/v1/quote"): client.BvnkClient.list_quotes,
    ("POST", "/api/v1/quote"): client.BvnkClient.create_quote,
    ("GET", "/api/v1/quote/{quote_uuid}"): client.BvnkClient.get_quote,
    ("PUT", "/api/v1/quote/accept/{quote_uuid}"): client.BvnkClient.accept_quote,
}


@pytest.fixture(scope="module")
def spec():
    response = httpx.get(f"{BASE_URL}/openapi.json", timeout=30)
    assert response.status_code == 200
    return response.json()


def test_every_endpoint_has_a_client_method(spec):
    in_spec = {(method.upper(), path) for path, ops in spec["paths"].items() for method in ops}
    assert in_spec - ENDPOINTS.keys() == set(), "endpoints in the spec with no client method"
    assert ENDPOINTS.keys() - in_spec == set(), "client methods for endpoints no longer in the spec"


def test_every_schema_has_a_model_with_its_required_fields(spec):
    for name, schema in spec["components"]["schemas"].items():
        model = getattr(models, name, None)
        assert model is not None, f"no model for schema {name}"
        fields = {f.alias or key for key, f in model.model_fields.items()}
        missing = set(schema.get("required", [])) - fields
        assert not missing, f"{name} model is missing required fields {missing}"


def test_every_model_field_is_as_strict_as_the_spec(spec):
    """Catches drift the name check above misses: a typed spec field left as `Any`, or a decimal string as plain Decimal."""
    for name, schema in spec["components"]["schemas"].items():
        model = getattr(models, name)
        fields = {f.alias or key: f for key, f in model.model_fields.items()}
        for prop, prop_schema in schema.get("properties", {}).items():
            field = fields.get(prop)
            assert field is not None, f"{name} model has no field for {prop}"
            if prop_schema.keys() & {"type", "$ref", "anyOf"}:
                assert field.annotation is not Any, f"{name}.{prop} is typed in the spec but Any in the model"
            if "pattern" in prop_schema:
                assert prop_schema["pattern"] == models.DECIMAL_PATTERN, f"{name}.{prop} has an unknown pattern"
                assert any(
                    isinstance(m, BeforeValidator) and m.func is models._decimal_string for m in field.metadata
                ), f"{name}.{prop} is a decimal string in the spec but not DecimalStr in the model"
