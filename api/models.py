"""Pydantic models for every schema published in the simulator's /openapi.json.

Fields the spec marks as required are required here, so a missing field fails validation.
Only fields the spec leaves untyped are typed as `Any`.
Deliberately stricter than the spec: `Quote.quoteStatus`/`paymentStatus` are enums and
`Quote.acceptanceDate` is `int | None`, to pin down the documented quote lifecycle.
"""

import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel

# The spec publishes money values as strings matching this pattern (pydantic's Decimal schema)
DECIMAL_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"


def _decimal_string(value: Any) -> Any:
    """Reject what plain Decimal would accept but the spec does not: JSON numbers, '1e5', ' 1 '."""
    if not isinstance(value, str) or not re.fullmatch(DECIMAL_PATTERN, value):
        raise ValueError(f"expected a decimal string matching {DECIMAL_PATTERN}, got {value!r}")
    return value


# A response field the spec types as a decimal string, parsed into Decimal
DecimalStr = Annotated[Decimal, BeforeValidator(_decimal_string)]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class QuoteStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    PAYMENT_OUT_PROCESSED = "PAYMENT_OUT_PROCESSED"
    EXPIRED = "EXPIRED"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    EXPIRED = "EXPIRED"


# --- account / system ---


class InitResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expiry: int = Field(gt=0)


class HealthMetrics(BaseModel):
    uptime: str
    approximate_db_size: str
    total_authenticated_requests: int


class EchoResponse(BaseModel):
    auth_token_expiry_time: str
    request_payload: Any


# --- wallet ---


class Options(ApiModel):
    address: str
    explorer: str
    transaction: str
    confirmations: int = Field(gt=0)


class Protocol(ApiModel):
    code: str
    network: str
    network_code: str


class Currency(ApiModel):
    id: int = Field(gt=0)
    code: str
    fiat: bool
    icon: str
    name: str
    withdrawal_parameters: list
    options: Options
    withdrawal_fee: DecimalStr
    deposit_fee: DecimalStr
    supports_deposits: bool
    supports_withdrawals: bool
    quantity_precision: int
    price_precision: int
    protocols: list[Protocol]


class Wallet(ApiModel):
    id: int = Field(gt=0)
    description: str
    currency: Currency
    supports_withdrawals: bool = False
    supports_deposits: bool = False
    custodian_wallet: Any
    supports_third_party: bool = False
    supports_internal_bvnk_network_transfers: bool = False
    partner: Any
    is_emoney: bool = False
    supported_transfer_destinations: list = []
    protocol: str
    address: str
    lookup: Any
    balance: DecimalStr
    available: DecimalStr
    withdrawal_fee: DecimalStr
    deposit_fee: DecimalStr
    converted_available: DecimalStr
    alternatives: list
    approx_available: DecimalStr
    approx_balance: DecimalStr
    approx_converted_available: DecimalStr
    lsid: str
    status: str


# --- quote ---


class QuoteRequest(ApiModel):
    """Request body for POST /api/v1/quote. Serialise with `to_body()`."""

    from_: str = Field(alias="from")
    to: str
    from_wallet: int
    to_wallet: int
    amount_in: Decimal | None = None
    amount_out: Decimal | None = None
    use_minimum: bool = False
    use_maximum: bool = False
    pay_in_method: str = "wallet"
    pay_out_method: str = "wallet"
    reference: str = "qa-test"

    def to_body(self) -> dict:
        body = self.model_dump(by_alias=True, exclude_none=True)
        # send amounts as strings so no precision is lost on the way
        for key in ("amountIn", "amountOut"):
            if key in body:
                body[key] = format(body[key], "f")
        return body


class FeeValue(ApiModel):
    service: DecimalStr
    processing: DecimalStr


class Fees(ApiModel):
    percentage: FeeValue
    value: FeeValue


class AccountMethod(ApiModel):
    id: int = Field(gt=0)
    display: Any


class UsePayInMethod(ApiModel):
    id: int = Field(gt=0)
    display: Any


class UsePayOutMethod(ApiModel):
    id: int = Field(gt=0)
    display: Any


class PayInMethod(ApiModel):
    id: int = Field(gt=0)
    code: str = "wallet"
    settlement_currency: str
    requested_currency: Any
    estimated_exchange_rate: Any
    account_methods: list = []


class PayOutMethod(ApiModel):
    id: int = Field(gt=0)
    code: str = "wallet"
    currency: str
    account_methods: list[AccountMethod]


class Quote(ApiModel):
    id: int = Field(gt=0)
    uuid: UUID
    from_: str = Field(alias="from")
    to: str
    amount_in: DecimalStr
    amount_due: DecimalStr
    amount_out: DecimalStr
    price: DecimalStr
    quote_status: QuoteStatus
    payment_status: PaymentStatus
    acceptance_expiry_date: int
    acceptance_date: int | None
    payment_expiry_date: int = Field(gt=0)
    payment_receipt_date: Any
    pay_in_legs: list
    pay_in_method: PayInMethod
    pay_out_method: PayOutMethod
    pay_out_instruction: Any
    pay_in_instruction: Any
    use_pay_in_method: UsePayInMethod
    use_pay_out_method: UsePayOutMethod
    fee: DecimalStr
    processing_fee: DecimalStr
    type: str
    net_price: DecimalStr
    gross_price: DecimalStr
    amount_in_gross: DecimalStr
    amount_in_net: DecimalStr
    fees: Fees
    date_created: int = Field(gt=0)
    last_updated: int = Field(gt=0)


# --- errors ---


class ValidationError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str
    input: Any = None
    ctx: dict | None = None


class HTTPValidationError(BaseModel):
    """422 body, as published in the spec."""

    detail: list[ValidationError]


class ErrorResponse(BaseModel):
    """Body of 400/401/404/412 errors. NOT in the spec: only 422 is documented."""

    detail: str
