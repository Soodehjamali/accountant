"""Custom SQLAlchemy column-type helpers for the Enterprise ERP (SIWRMS).

This module turns the **precision and length contracts** already encoded in
``database.constants`` (``NumericPrecision`` and ``StringLength``) into
fully-parameterized SQLAlchemy column-type factories, so that future ORM
models never re-hard-code magic numbers like ``Numeric(18, 4)`` or
``String(40)``.

Two families:

* ``Numeric`` factories — parameterized from ``NumericPrecision`` members.
* ``String`` factories   — parameterized from ``StringLength`` members.

Scope & contract:

* The factories return **plain ``sqlalchemy.Numeric`` / ``sqlalchemy.String``
  instances** — NOT ``TypeDecorator`` subclasses (the task explicitly prefers
  plain aliases/factories unless decoration is truly needed; it is not —
  these are straight parameterizations of built-in types).
* Every parameter is read from ``NumericPrecision`` / ``StringLength``; **no
  precision/length value is invented here** — if a new precision is needed it
  must be added to ``database/constants.py`` first. This module only *consumes*
  those constants.
* ``Numeric(..., asdecimal=True)`` (the SQLAlchemy default) is preserved so
  money / cost / quantity / rate values round-trip as Python ``decimal.Decimal``
  — the spec's "Decimal precision rules / no float for money" policy is enforced
  by reading these and only ever assigning :class:`Decimal` (or ``int``).
* All factories carry full type hints; return types are declared as
  ``sqlalchemy.Numeric`` / ``sqlalchemy.String`` (their concrete base classes
  the constructors return), not ``Any``.

OUT OF SCOPE (and therefore NOT present): custom ``TypeDecorator`` types
(e.g. a SHA-256 ``Char`` hash-chain type, JSONB/INET wrappers, enum SQL
schemas), ``ForeignKey`` helpers, models, sessions, Alembic.

Authority:
    - 07_DATABASE_SPEC.md  (repeated ``NUMERIC(18,4)`` / ``NUMERIC(18,6)``
      / ``NUMERIC(7,4)`` / ``NUMERIC(9,6)`` and ``VARCHAR(N)`` column shapes).
    - database/constants.py (``NumericPrecision`` and ``StringLength`` are
      the single source of truth for precision/scale and string width).

Deliberate omission — flagged for awareness (NOT an error):
    ``database.constants.HASH_HEX_LENGTH`` (= 64, the ``CHAR(64)`` SHA-256
    hash-chain column width) is a *module-level* constant, not a member of
    ``NumericPrecision`` or ``StringLength``. The task scope restricts this
    module to the two named classes' members, so no ``Char(HASH_HEX_LENGTH)``
    helper is provided here. A future ``types.py`` expansion (or a dedicated
    hash-chain ``TypeDecorator``) may consume it; that is out of this scope.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import Numeric, String

from database.constants import NumericPrecision, StringLength


# ===========================================================================
# NUMERIC factories — consume NumericPrecision members
# ===========================================================================
#: Precision/scale pair taken straight from ``NumericPrecision`` — used only to
#: build the factories below and to keep the (precision, scale) coupling visible
#: at the call site. Read-only; no value invented.


def money_type() -> Numeric:
    """``NUMERIC(18, 4)`` — money totals & signed transaction quantities.

    Maps to ``NumericPrecision.PRECISION_MONEY`` / ``NumericPrecision.SCALE_MONEY``.

    Used by every money column and every ``signed_quantity`` / ``qty_*`` column
    in the spec (e.g. ``inventory_transaction.signed_quantity``,
    ``invoice.grand_total``, ``order.subtotal``). ``asdecimal=True`` ensures
    values arrive as :class:`decimal.Decimal`.
    """

    return Numeric(
        precision=NumericPrecision.PRECISION_MONEY,
        scale=NumericPrecision.SCALE_MONEY,
    )


def cost_type() -> Numeric:
    """``NUMERIC(18, 6)`` — snapshot cost types.

    Maps to ``NumericPrecision.PRECISION_COST`` / ``NumericPrecision.SCALE_COST``.

    Used by cost-snapshot columns that need six decimals to preserve per-unit
    economy (e.g. ``inventory_transaction.unit_cost``,
    ``transfer_line.unit_cost``, ``shipment_line.unit_cost_at_ship``).
    """

    return Numeric(
        precision=NumericPrecision.PRECISION_COST,
        scale=NumericPrecision.SCALE_COST,
    )


def rate_type() -> Numeric:
    """``NUMERIC(7, 4)`` — rate / percentage types bounded 0..100.

    Maps to ``NumericPrecision.PRECISION_RATE`` / ``NumericPrecision.SCALE_RATE``.

    Used by percentage-rate columns (e.g. ``invoice_line.tax_rate``,
    ``commission_transaction.rate_applied``, discount percent). The 0..100
    bound itself is expressed as a ``CHECK`` constraint on the model, not here —
    this factory supplies only the type's precision/scale.
    """

    return Numeric(
        precision=NumericPrecision.PRECISION_RATE,
        scale=NumericPrecision.SCALE_RATE,
    )


def geo_type() -> Numeric:
    """``NUMERIC(9, 6)`` — geo-tracking latitude / longitude.

    Maps to ``NumericPrecision.PRECISION_GEO`` / ``NumericPrecision.SCALE_GEO``.

    Used by ``shipment_status_history.lat`` / ``shipment_status_history.lng``
    (per the spec: ``lat NUMERIC(9,6)`` / ``lng NUMERIC(9,6)``, with a
    ``CHECK`` constraining lat to ``[-90, 90]`` and lng to ``[-180, 180]`` on
    the model). This factory supplies the type only; bounds are model-layer
    concerns.
    """

    return Numeric(
        precision=NumericPrecision.PRECISION_GEO,
        scale=NumericPrecision.SCALE_GEO,
    )


# ===========================================================================
# STRING factories — consume StringLength members
# ===========================================================================
def _string(length: int) -> String:
    """Build a ``VARCHAR(length)`` from a :class:`StringLength` member.

    Private helper — the public string factories below each forward one
    ``StringLength`` member so the source of every length is explicit and
    discoverable at the call site, with no invented widths.
    """

    return String(length=length)


def name_type() -> String:
    """``VARCHAR(160)`` — human display names.

    Maps to ``StringLength.NAME`` (product.name, warehouse.name, full names).
    """

    return _string(StringLength.NAME)


def business_key_type() -> String:
    """``VARCHAR(40)`` — primary business-document key.

    Maps to ``StringLength.BUSINESS_KEY`` (order/transfer/invoice/payment/credit
    note/adjustment/return/shipment business-key ``*_number`` columns).
    """

    return _string(StringLength.BUSINESS_KEY)


def state_token_type() -> String:
    """``VARCHAR(16)`` — short state / channel / type token.

    Maps to ``StringLength.STATE_TOKEN`` (ShipmentState, small enums, channels).
    """

    return _string(StringLength.STATE_TOKEN)


def state_token_long_type() -> String:
    """``VARCHAR(24)`` — extended state token.

    Maps to ``StringLength.STATE_TOKEN_LONG`` (TransferState / OrderState, which
    use up to 24 characters per the spec).
    """

    return _string(StringLength.STATE_TOKEN_LONG)


def type_token_type() -> String:
    """``VARCHAR(40)`` — polymorphic / enum-style token discriminator.

    Maps to ``StringLength.TYPE_TOKEN`` (polymorphic ``reference_type`` /
    ``entity_type`` discriminators and growing enum tokens).
    """

    return _string(StringLength.TYPE_TOKEN)


def code_short_type() -> String:
    """``VARCHAR(40)`` — short code.

    Maps to ``StringLength.CODE_SHORT`` (SKU / warehouse code / currency
    ISO-3 / short codes). The 40-width is the spec's value even for fields the
    spec calls "~12 chars" — the source constant reads 40 and is authoritative.
    """

    return _string(StringLength.CODE_SHORT)


def token_type() -> String:
    """``VARCHAR(120)`` — ephemeral tokens.

    Maps to ``StringLength.TOKEN`` (session tokens, tracking-adjacent tokens,
    bank references, mime-type-adjacent values).
    """

    return _string(StringLength.TOKEN)


def tracking_number_type() -> String:
    """``VARCHAR(80)`` — carrier tracking number.

    Maps to ``StringLength.TRACKING_NUMBER`` (carrier-specific, may be longer
    than a generic token).
    """

    return _string(StringLength.TRACKING_NUMBER)


def description_type() -> String:
    """``VARCHAR(255)`` — description / line description.

    Maps to ``StringLength.DESCRIPTION`` (``invoice_line.description``,
    ``credit_note_line.description``, free-text line descriptions).
    """

    return _string(StringLength.DESCRIPTION)


def storage_key_type() -> String:
    """``VARCHAR(512)`` — storage key / URI.

    Maps to ``StringLength.STORAGE_KEY`` (S3-compatible object keys for
    ``attachment.storage_key`` / ``generated_document.storage_key``).
    """

    return _string(StringLength.STORAGE_KEY)


def mime_type_type() -> String:
    """``VARCHAR(120)`` — mime-type / format token.

    Maps to ``StringLength.MIME_TYPE`` (``attachment.mime_type`` and adjacent
    format tokens).
    """

    return _string(StringLength.MIME_TYPE)


def cron_expression_type() -> String:
    """``VARCHAR(60)`` — cron expression for report schedules.

    Maps to ``StringLength.CRON_EXPRESSION``
    (``report_definition.schedule_cron``).
    """

    return _string(StringLength.CRON_EXPRESSION)


__all__: Final[list[str]] = [
    # NUMERIC helpers
    "money_type",
    "cost_type",
    "rate_type",
    "geo_type",
    # STRING helpers
    "name_type",
    "business_key_type",
    "state_token_type",
    "state_token_long_type",
    "type_token_type",
    "code_short_type",
    "token_type",
    "tracking_number_type",
    "description_type",
    "storage_key_type",
    "mime_type_type",
    "cron_expression_type",
]
