"""``T10 — order`` ORM model (sales order header).

Authority: ``07_DATABASE_SPEC.md`` §T10 — ``T10 — order`` **has** a full
detailed section in the physical spec (unlike most M-tables so far), so the
spec is primary authority here; ``06_ERD.md`` (F.4 — Sales / Order) is
secondary/corroborating only::

    T10 — order
    Purpose: Sales order header (SRS E15).
    PK: id (UUID)
    FK: customer_id -> customer.id; representative_id -> representative.id;
        created_by -> app_user.id; fulfillment_warehouse_id -> warehouse.id
        (nullable); currency_id -> currency.id;
        customer_city_ref_id -> city_ref.id; rep_city_ref_id -> city_ref.id
    Unique: uq_order_number (order_number)
    Check: ck_order_type (order_type IN ('LOCAL','DIRECT'));
        ck_order_fulfillment_mode (fulfillment_mode IN
        ('REP_LOCAL','FACTORY_DIRECT'));
        ck_order_state (state IN (...13 OrderState values...));
        ck_order_totals_nonneg (subtotal >= 0 AND discount_total >= 0 AND
        tax_total >= 0 AND grand_total >= 0)
    Business constraints: order_type classification per BR-S3 (manual
        overrides written to audit_log); LOCAL orders require a
        stock_reservation before leaving DRAFT; DIRECT orders must never
        post against a representative's warehouse ledger (service-layer);
        state transitions are guarded by an application-level state
        machine, mirrored into order_status_history (T12) -- the database
        enforces only that `state` is one of the valid enum values, not
        the transition graph itself.
    Soft Delete Strategy: Supported, though cancellation should prefer
        state='CANCELLED' over deletion.
    Notes: `order` is a reserved word; the physical table name is an
        implementation decision this spec explicitly leaves open.

Reserved-word table name -- decision made and justified here:
    The spec's own Notes section (point 15) explicitly flags ``order`` as a
    reserved SQL keyword and leaves the physical table-name choice open:
    literal ``__tablename__ = "order"`` (quoted automatically by SQLAlchemy
    wherever required) vs. ``__tablename__ = "sales_order"`` while keeping
    the Python class named ``Order``. **This model chooses literal
    ``"order"``.** Reasoning: every downstream table the spec itself defines
    already writes FK targets as literal ``order.id`` -- T11 ``order_line``
    (``order_id -> order.id``) and T12 ``order_status_history``
    (``order_id -> order.id``) -- so choosing ``sales_order`` here would
    create a mismatch between this table's actual name and every other
    table's spec-declared FK target, forcing either the spec's own FK
    descriptions to be reinterpreted or a permanent naming exception to be
    remembered project-wide. SQLAlchemy's dialect-aware identifier preparer
    auto-quotes reserved identifiers for the target dialect (PostgreSQL)
    automatically, in every DDL statement and FK ``REFERENCES`` clause,
    with no extra configuration required on this model or any table that
    later references it -- so the "needs quoting" cost the spec's Notes
    section raises is a non-issue in practice. The Python class name stays
    ``Order`` either way.

CRITICAL naming trap -- ``order_number``'s unique constraint:
    The spec's literal constraint name is ``uq_order_number``. The
    project's *usual* idiom -- column-level ``unique=True`` on
    ``order_number`` -- would NOT produce that name here: ``NAMING_
    CONVENTION["uq"]`` is ``uq_%(table_name)s_%(column_0_name)s``, and
    because the table is itself named ``order``, the implicit path would
    render ``uq_order_order_number`` (table name + column name, both
    containing "order", concatenated in full) -- doubling the word rather
    than collapsing it. This is the first table in the codebase where a
    column name embeds its own table name, so it is the first place this
    latent collision actually surfaces. To get the spec's literal
    ``uq_order_number``, this model uses an **explicit**
    ``UniqueConstraint("order_number", name=uq_index_name("order",
    "number"))`` instead of column-level ``unique=True`` -- passing the
    helper a bare descriptor of ``"number"`` (not ``"order_number"``) so
    ``uq_index_name`` assembles ``uq_`` + ``order`` + ``number`` ->
    ``uq_order_number`` exactly, without the doubled segment. Flagged
    explicitly so a future edit doesn't "clean this up" back to
    column-level ``unique=True``, silently reintroducing the doubled name.

Soft-delete tension between the two source docs -- flagged, not silently
resolved:
    ``06_ERD.md``'s own T10 line reads plain ``"Classification: T"`` -- no
    ``"+ soft-deletable"`` qualifier, which is the pattern this codebase has
    used elsewhere (``product``, ``customer``) to decide whether
    ``deleted_at`` is added. ``07_DATABASE_SPEC.md`` §T10 point 12,
    however, explicitly states: *"Soft Delete Strategy: Supported, though
    cancellation should prefer `state='CANCELLED'` over deletion to
    preserve the historical record."* Since the detailed spec is this
    table's primary authority (per the module docstring's opening note),
    ``deleted_at`` **is** added here -- but this ERD/spec discrepancy is
    recorded explicitly rather than resolved silently in either direction.
    Operationally, the spec's own guidance is that ``state='CANCELLED'``
    should be preferred over an actual soft-delete for the ordinary
    cancellation path; ``deleted_at`` remains available for the cases that
    are not a state-machine transition (e.g. erroneous test/draft rows).

``created_by`` / UAC overlap -- a known, PRE-EXISTING gap, NOT fixed here:
    The spec's own FK list separately names ``created_by -> app_user.id``
    as one of ``order``'s Foreign Keys. This is the *same* column
    ``UniversalAuditColumns`` (UAC, which this model uses) already
    supplies -- UAC's ``created_by`` is already ``NOT NULL`` by design
    (unlike AAC's nullable version), so there is no field-shape conflict;
    the spec is simply restating a mixin-supplied column for emphasis. This
    model therefore does **not** declare a second ``created_by`` column
    (doing so would collide with UAC's). HOWEVER: UAC's ``created_by`` is
    still a plain ``UUID`` with **no** ``ForeignKey()`` -- ``mixins.py``
    documents this as deferred "until app_user lands," and ``app_user`` has
    now landed in this codebase. Retrofitting UAC's ``created_by`` /
    ``updated_by`` into real FKs is a separate, codebase-wide follow-up
    (it touches every model that uses UAC or AAC) and is explicitly OUT OF
    SCOPE here -- ``database/mixins.py`` is not touched by this change.

Audit-column family -- ``UniversalAuditColumns`` (UAC):
    ``created_at`` / ``updated_at`` / ``created_by`` / ``updated_by`` /
    ``version``. ``Order`` uses UAC and opts its ``version`` column into
    SQLAlchemy optimistic locking, same as every other UAC model.

Column-type choices:

* ``order_number`` -- ``business_key_type()`` -> ``VARCHAR(40)``, matching
  the spec's ``VARCHAR(40)``. Chosen over ``code_short_type()`` (also
  ``VARCHAR(40)``) on semantic grounds, not width: ``business_key_type()``'s
  own docstring names its purpose as *"primary business-document key ...
  order/transfer/invoice/payment/credit note/adjustment/return/shipment
  business-key ``*_number`` columns"* -- ``order.order_number`` is exactly
  the case that docstring calls out by name. ``code_short_type()``'s own
  docstring instead describes *"SKU / warehouse code / currency ISO-3 /
  short codes"* -- a different semantic (short controlled-vocabulary code,
  not a generated business-document number). ``business_key_type()`` is the
  precise, non-reflexive fit.
* ``sales_channel`` / ``fulfillment_mode`` -- ``state_token_long_type()``
  -> ``VARCHAR(24)``, used as the closest existing factory for the spec's
  ``VARCHAR(20)``: no exact 20-width factory exists in ``database.types``.
  Flagged as a placeholder, same treatment every other closest-fit case in
  this codebase has received (e.g. ``warehouse.address`` via
  ``description_type()``). ``sales_channel``'s CHECK vocabulary
  (``BOT_WEB`` / ``BOT_TELEGRAM`` / ``BOT_BALE`` / ``OFFICE``) is a
  *distinct* vocabulary from PART A's separate ``BotPlatform`` enum
  (``TELEGRAM`` / ``BALE`` / ``WEBCHAT``) despite superficial overlap in
  naming -- they are not interchangeable and are not conflated here.
* ``order_type`` -- ``state_token_type()`` -> ``VARCHAR(16)``, an exact
  match to the spec's ``VARCHAR(16)``.
* ``state`` -- ``state_token_long_type()`` -> ``VARCHAR(24)``, an *exact*
  match to the spec's ``VARCHAR(24)`` this time (not a placeholder, unlike
  ``sales_channel`` / ``fulfillment_mode`` above).
* ``subtotal`` / ``discount_total`` / ``tax_total`` / ``grand_total`` --
  ``money_type()`` -> ``NUMERIC(18, 4)``, ``NOT NULL DEFAULT 0``. Mirrors
  ``customer.credit_limit_amount``'s ``default=0`` /
  ``server_default=sa_text("0")`` pattern exactly.
* ``customer_city_ref_id`` / ``rep_city_ref_id`` -- nullable FKs to
  ``city_ref``, declared with the same explicit ``_SAUuid(as_uuid=True)`` +
  ``ForeignKey(...)`` two-positional-argument shape ``customer.py`` uses for
  its own ``city_ref_id`` (the spec's *"Snapshot for Scenario A/B audit"*
  note is the direct analogue of that existing column).

Naming convention:
    ``order_number``'s unique constraint is the naming-trap case explained
    above -- ``uq_index_name("order", "number")``, NOT column-level
    ``unique=True``. Every CHECK below uses ``ck_index_name`` normally: for
    this table the standard helper output happens to already match the
    spec's literal names verbatim (``ck_order_type``,
    ``ck_order_fulfillment_mode``, ``ck_order_state``,
    ``ck_order_totals_nonneg``) -- no override is needed, unlike some of
    ``inventory_transaction``'s spec-literal cases. Every FK uses
    ``fk_index_name`` normally. The two composite indexes use
    ``idx_index_name`` with ``composite_descriptor`` -- no literal override
    needed. The partial index ``idx_order_open`` is likewise produced by
    plain ``idx_index_name("order", "open")`` with no override -- the
    helper's normal output already matches the spec's literal name.

Out of scope for this model (not implemented here):
    * The order-state transition graph -- the spec explicitly states this
      is application-level, not a DB CHECK (only membership in the
      OrderState enum is enforced here).
    * ``order_status_history`` (T12) -- a separate table/task.
    * Range partitioning by ``ordered_at`` -- the spec marks this
      optional/future, not required at initial scale.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Uuid as _SAUuid
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func

from database.base import Base, GuidPk, id_column
from database.mixins import UniversalAuditColumns
from database.naming import ck_index_name, composite_descriptor, fk_index_name, idx_index_name, uq_index_name
from database.types import business_key_type, money_type, state_token_long_type, state_token_type


class Order(Base, UniversalAuditColumns):
    """``T10 — order`` — sales order header (Classification: T, soft-deletable per spec — see module docstring)."""

    # Literal reserved-word table name -- see module docstring's
    # "Reserved-word table name" section for the justification. SQLAlchemy
    # auto-quotes this for PostgreSQL wherever it is emitted.
    __tablename__ = "order"

    @declared_attr

    def __mapper_args__(cls) -> dict:

        return {"version_id_col": cls.version}
    # ------------------------------------------------------------------ id
    id: GuidPk = id_column()

    # ------------------------------------------------------------ order_number
    # Unique via an explicit UniqueConstraint below -- NOT column-level
    # unique=True. See the module docstring's "CRITICAL naming trap" note.
    order_number: Mapped[str] = mapped_column(
        business_key_type(),
        nullable=False,
    )

    # ------------------------------------------------------------- customer_id
    customer_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "customer.id",
            name=fk_index_name("order", "customer_id", "customer"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------- representative_id
    representative_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "representative.id",
            name=fk_index_name("order", "representative_id", "representative"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- sales_channel
    # Placeholder width -- see module docstring's column-type-choices note.
    # CHECK vocabulary is distinct from PART A's BotPlatform enum.
    sales_channel: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # NOTE: no separate `created_by` column here -- UniversalAuditColumns
    # already supplies it (NOT NULL by design). See the module docstring's
    # "created_by / UAC overlap" section.

    # ------------------------------------------------------ fulfillment_warehouse_id
    # Nullable -- "set once reserved" per spec.
    fulfillment_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "warehouse.id",
            name=fk_index_name("order", "fulfillment_warehouse_id", "warehouse"),
        ),
        nullable=True,
    )

    # --------------------------------------------------------------- order_type
    order_type: Mapped[str] = mapped_column(
        state_token_type(),
        nullable=False,
    )

    # --------------------------------------------------------- fulfillment_mode
    # Placeholder width -- see module docstring's column-type-choices note.
    fulfillment_mode: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
    )

    # -------------------------------------------------------------------- state
    # Exact-width match to the spec's VARCHAR(24) -- not a placeholder.
    # First quoted-string server_default in this codebase; mirrors the
    # existing default+server_default dual-declaration pattern used
    # elsewhere (e.g. currency.is_base, customer.credit_limit_amount) but
    # for a string literal rather than a boolean/numeric one.
    state: Mapped[str] = mapped_column(
        state_token_long_type(),
        nullable=False,
        default="DRAFT",
        server_default=sa_text("'DRAFT'"),
    )

    # ------------------------------------------------------------------ currency_id
    currency_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "currency.id",
            name=fk_index_name("order", "currency_id", "currency"),
        ),
        nullable=False,
    )

    # ------------------------------------------------------------- money totals
    # Mirrors customer.credit_limit_amount's default=0 / server_default
    # pattern exactly (see module docstring).
    subtotal: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    discount_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    tax_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    grand_total: Mapped[decimal.Decimal] = mapped_column(
        money_type(),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    # ---------------------------------------------------------------- ordered_at
    ordered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ---------------------------------------------------- shipped/invoiced/paid_at
    shipped_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invoiced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paid_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ------------------------------------------------- customer_city_ref_id
    # Nullable FK -- spec: "Snapshot for Scenario A/B audit". Same
    # declaration shape as customer.py's own city_ref_id (see module
    # docstring).
    customer_city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("order", "customer_city_ref_id", "city_ref"),
        ),
        nullable=True,
    )

    # ------------------------------------------------------ rep_city_ref_id
    rep_city_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "city_ref.id",
            name=fk_index_name("order", "rep_city_ref_id", "city_ref"),
        ),
        nullable=True,
    )

    # --------------------------------------------------------- price_list_id
    # NOT NULL FK to price_list — determines which price list is used
    # to resolve product prices for this order's lines. Set at order
    # creation time and immutable thereafter (the order's pricing
    # provenance). Every order must reference a price list so that
    # order lines can resolve their unit prices deterministically.
    price_list_id: Mapped[uuid.UUID] = mapped_column(
        _SAUuid(as_uuid=True),
        ForeignKey(
            "price_list.id",
            name=fk_index_name("order", "price_list_id", "price_list"),
        ),
        nullable=False,
    )

    # -------------------------------------------------------------- deleted_at
    # See module docstring's "Soft-delete tension" section: 07_DATABASE_
    # SPEC.md (primary authority for this table) supports soft delete;
    # 06_ERD.md's classification line does not flag it. Added per the
    # spec, discrepancy flagged rather than silently resolved.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # UNIQUE -- see module docstring's "CRITICAL naming trap" section.
        # Descriptor is "number" (not "order_number") so the assembled name
        # is uq_order_number, not the doubled uq_order_order_number that
        # column-level unique=True's implicit convention would produce.
        UniqueConstraint(
            "order_number",
            name=uq_index_name("order", "number"),
        ),
        # CHECK: order_type vocabulary. Standard ck_index_name usage happens
        # to already match the spec's literal ck_order_type verbatim.
        CheckConstraint(
            "order_type IN ('LOCAL', 'DIRECT')",
            name=ck_index_name("order", "type"),
        ),
        # CHECK: fulfillment_mode vocabulary.
        CheckConstraint(
            "fulfillment_mode IN ('REP_LOCAL', 'FACTORY_DIRECT')",
            name=ck_index_name("order", "fulfillment_mode"),
        ),
        # CHECK: full 13-value OrderState vocabulary, transcribed verbatim
        # from the spec.
        CheckConstraint(
            "state IN ("
            "'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'RESERVED', "
            "'FULFILLING', 'SHIPPED', 'INVOICED', 'PAID', 'COMPLETED', "
            "'CANCELLED', 'BACKORDERED', 'PARTIALLY_FULFILLED', 'RETURNED'"
            ")",
            name=ck_index_name("order", "state"),
        ),
        # CHECK: all four money totals >= 0, as ONE combined constraint --
        # the spec gives this as a single constraint, not four separate
        # per-column CHECKs.
        CheckConstraint(
            "subtotal >= 0 AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0",
            name=ck_index_name("order", "totals_nonneg"),
        ),
        # Recommended single-column indexes.
        Index(
            idx_index_name("order", "customer_id"),
            "customer_id",
        ),
        Index(
            idx_index_name("order", "representative_id"),
            "representative_id",
        ),
        Index(
            idx_index_name("order", "state"),
            "state",
        ),
        Index(
            idx_index_name("order", "price_list_id"),
            "price_list_id",
        ),
        # Composite indexes -- named dashboard/queue query patterns.
        Index(
            idx_index_name("order", composite_descriptor(("customer_id", "state"))),
            "customer_id",
            "state",
        ),
        Index(
            idx_index_name("order", composite_descriptor(("representative_id", "state"))),
            "representative_id",
            "state",
        ),
        # Partial index -- open (not completed/cancelled) orders per rep.
        Index(
            idx_index_name("order", "open"),
            "representative_id",
            postgresql_where=sa_text("state NOT IN ('COMPLETED', 'CANCELLED')"),
        ),
    )


__all__ = ["Order"]
