"""Bot endpoints: ``POST /bot/verify-phone``, ``GET/POST /bot/reps/{rep_id}/...``, ``POST /bot/logout``.

The ``/bot/verify-phone`` endpoint is the entry point for the phone-based
bot authentication flow (ADR-013).  All other ``/bot/`` endpoints require a
valid bot JWT token (via ``require_bot_rep_scope`` + ``require_bot_permission``).

Authorization model (server-side only -- never trusted to the messenger UI):
    - ``verify-phone`` is the login step: it binds the platform identity
      (``platform`` + ``chat_id``) to an ACTIVE representative's phone and
      issues a short-lived JWT carrying both ``representative_id`` and
      ``session_id``.
    - Read endpoints (inventory, reports, customers, products,
      price-preview) require ``BOT_QUERY``.
    - Write endpoints (order creation, invoice creation) require
      ``BOT_WRITE``.

Order-creation endpoints (``customers`` / ``products`` / ``price-preview``
/ ``orders``) support the representative order workflow: the rep picks a
scoped customer and in-stock products, the ERP resolves the selling price
(BR-P1) server-side, and ``POST /bot/reps/{rep_id}/orders`` creates a
DRAFT order through ``order_service.create_order`` -- Telegram never
supplies a price or a representative id.
    - ``rep_id`` is extracted from the JWT token (not from the URL
      parameter) to prevent IDOR -- the URL parameter is only used for
      readability/documentation.

Every bot action is written to ``audit_log`` (entity_type ``bot_query`` /
``bot_verify_phone`` / ``bot_session``), identifying the representative and
platform without storing tokens or phone numbers unnecessarily.
"""

from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.bot_auth import (
    get_bot_representative,
    require_bot_permission,
    require_bot_rep_scope,
)
from app.dependencies.db import get_db
from app.schemas.bot import (
    BotCustomer,
    BotCustomerListResponse,
    BotErrorResponse,
    BotInvoiceCreateRequest,
    BotInvoiceResponse,
    BotInventoryResponse,
    BotInventoryItem,
    BotOrderCreateRequest,
    BotOrderCreateResponse,
    BotOrderLineResponse,
    BotPricePreviewResponse,
    BotProduct,
    BotProductListResponse,
    BotReportResponse,
    BotReportSummary,
    BotVerifyPhoneRequest,
    BotVerifyPhoneResponse,
)
from database.models.product import Product
from database.models.representative import Representative
from services import (
    audit_service,
    bot_phone_service,
    inventory_service,
    order_service,
    price_list_service,
    representative_scope_service,
)
from services import (
    audit_service,
    bot_phone_service,
    inventory_service,
    order_service,
    representative_scope_service,
)

router = APIRouter(prefix="/bot", tags=["bot"])

#: Permission gates for the bot REST endpoints (ADR-008 vocabulary).
BOT_QUERY_PERMISSION = "BOT_QUERY"
BOT_WRITE_PERMISSION = "BOT_WRITE"

_require_bot_query = require_bot_permission(BOT_QUERY_PERMISSION)
_require_bot_write = require_bot_permission(BOT_WRITE_PERMISSION)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _bot_actor_user_id(db: Session, rep_id: uuid.UUID) -> uuid.UUID | None:
    """Return the ``AppUser`` linked to the representative, if any.

    Bot actions are performed by a Representative, not a logged-in AppUser;
    when the representative has a linked login user we attribute the audit
    row to it, otherwise the actor stays NULL (system-side action).
    """
    from services.bot_command_service import _find_user_by_representative

    app_user = _find_user_by_representative(db, rep_id)
    return app_user.id if app_user is not None else None


def _audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    rep: Representative | None,
    after: dict | None = None,
) -> None:
    """Append one audit row for a bot action, if the action is valid.

    The audit vocabulary was extended (AUTHENTICATE/QUERY/ATTEMPT) for the
    bot flow; anything else falls through to the standard actions.
    """
    actor = _bot_actor_user_id(db, rep.id) if rep is not None else None
    audit_service.record(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor,
        after=after,
    )
    db.flush()


# ---------------------------------------------------------------------------
# POST /bot/verify-phone
# ---------------------------------------------------------------------------


@router.post(
    "/verify-phone",
    response_model=BotVerifyPhoneResponse,
    summary="Verify phone number and return bot access token",
    responses={
        404: {"model": BotErrorResponse, "description": "Phone not found"},
        403: {"model": BotErrorResponse, "description": "Representative inactive"},
    },
)
def verify_phone(
    body: BotVerifyPhoneRequest,
    db: Session = Depends(get_db),
) -> BotVerifyPhoneResponse:
    """Verify a representative's phone number and issue a bot JWT token.

    The bot sends this when a representative shares their contact
    information via the Telegram/Bale "Share Phone" button.

    On success:
    - the persistent ``bot_session`` is created/updated, binding
      ``(platform, chat_id)`` to the verified representative,
    - a short-lived JWT (30 minutes) is returned containing the
      ``representative_id`` (``sub``) and the ``session_id`` claim so the
      auth dependency can reject revoked/expired sessions immediately.
    """
    settings = get_settings()
    platform = body.platform.upper()

    try:
        result = bot_phone_service.verify_phone(
            db,
            phone_number=body.phone_number,
            platform=platform,
            chat_id=body.chat_id,
            secret_key=settings.secret_key,
        )
    except bot_phone_service.PhoneNotFoundError as exc:
        _audit(
            db,
            entity_type="bot_verify_phone",
            entity_id=uuid.UUID(int=0),
            action="AUTHENTICATE",
            rep=None,
            after={"platform": platform, "result": "failed", "reason": "phone_not_found"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except bot_phone_service.RepresentativeInactiveError as exc:
        _audit(
            db,
            entity_type="bot_verify_phone",
            entity_id=uuid.UUID(int=0),
            action="AUTHENTICATE",
            rep=None,
            after={"platform": platform, "result": "failed", "reason": "inactive"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except bot_phone_service.InvalidPlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    _audit(
        db,
        entity_type="bot_verify_phone",
        entity_id=result.representative_id,
        action="AUTHENTICATE",
        rep=None,
        after={"platform": platform, "result": "ok"},
    )
    db.commit()
    return BotVerifyPhoneResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        representative_id=result.representative_id,
        representative_name=result.representative_name,
    )


# ---------------------------------------------------------------------------
# POST /bot/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current bot session (logout)",
    responses={401: {"model": BotErrorResponse}},
)
def bot_logout(
    request: Request,
    rep: Representative = Depends(get_bot_representative),
    db: Session = Depends(get_db),
) -> None:
    """Revoke the persistent bot session bound to the presented JWT.

    After this call the token's ``session_id`` is ``REVOKED`` and every
    subsequent request with that token is rejected -- the representative
    must re-verify their phone to get a new session.
    """
    from fastapi.security.utils import get_authorization_scheme_param

    from app.dependencies.bot_auth import _decode_bot_token
    from services import bot_session_service

    settings = get_settings()

    # ``get_bot_representative`` already validated the token; re-read the
    # raw Authorization header to recover the session_id claim.
    authorization = request.headers.get("Authorization", "")
    _, token = get_authorization_scheme_param(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    _, session_id = _decode_bot_token(token, secret_key=settings.secret_key)

    if session_id is not None:
        try:
            bot_session = bot_session_service.revoke_session_by_id(
                db, uuid.UUID(session_id), revoked_by=_get_system_user_id(db),
            )
        except (bot_session_service.SessionNotLinkedError, ValueError):
            bot_session = None
        if bot_session is not None:
            _audit(
                db,
                entity_type="bot_session",
                entity_id=bot_session.id,
                action="UPDATE",
                rep=rep,
                after={"status": "REVOKED", "reason": "user_logout"},
            )
    db.commit()


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/inventory
# ---------------------------------------------------------------------------


@router.get(
    "/reps/{rep_id}/inventory",
    response_model=BotInventoryResponse,
    summary="Get inventory for the representative's assigned warehouse",
    responses={403: {"model": BotErrorResponse}},
)
def get_rep_inventory(
    rep_id: uuid.UUID,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_query),
    db: Session = Depends(get_db),
) -> BotInventoryResponse:
    """Return inventory balances for the representative's primary warehouse.

    ``rep_id`` from the URL is validated against the JWT token by
    ``require_bot_rep_scope``; the caller must hold ``BOT_QUERY``.  All data
    access goes through the existing scope service layer (ADR-007).
    """
    warehouses = representative_scope_service.resolve_representative_warehouses(
        db, rep.id, primary_only=True,
    )

    if not warehouses:
        _audit(
            db,
            entity_type="bot_query",
            entity_id=rep.id,
            action="QUERY",
            rep=rep,
            after={"resource": "inventory", "warehouse": None},
        )
        db.commit()
        return BotInventoryResponse(items=[], warehouse_code="N/A")

    wh = warehouses[0]
    balances = inventory_service.list_warehouse_balances(
        db, warehouse_id=wh.id, limit=50,
    )

    items = [
        BotInventoryItem(
            sku=b["sku"],
            name=b["name"],
            balance=int(b["balance"]),
            warehouse_code=wh.code,
        )
        for b in balances
    ]

    _audit(
        db,
        entity_type="bot_query",
        entity_id=rep.id,
        action="QUERY",
        rep=rep,
        after={"resource": "inventory", "warehouse": wh.code, "items": len(items)},
    )
    db.commit()

    return BotInventoryResponse(items=items, warehouse_code=wh.code)


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/reports
# ---------------------------------------------------------------------------


@router.get(
    "/reps/{rep_id}/reports",
    response_model=BotReportResponse,
    summary="Get sales report for the representative",
    responses={403: {"model": BotErrorResponse}},
)
def get_rep_reports(
    rep_id: uuid.UUID,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_query),
    db: Session = Depends(get_db),
    period: str = Query(
        default="this_month",
        pattern="^(today|this_week|this_month)$",
        description="Report window: today, this_week, or this_month (default).",
    ),
) -> BotReportResponse:
    """Return a sales/performance report for the representative.

    Aggregates order count and revenue directly in the database (scoped by
    ``Order.representative_id``) plus the representative's assigned
    customer count (ADR-007 scope service).  Periods: ``today``,
    ``this_week``, ``this_month`` (default).
    """
    from sqlalchemy import func, select
    from database.models.order import Order

    now = datetime.datetime.now(datetime.timezone.utc)

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "امروز"
    elif period == "this_week":
        start = (now - datetime.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        label = "این هفته"
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = "این ماه"

    revenue_states = ("SHIPPED", "INVOICED", "PAID", "COMPLETED")

    # Order count for the window + revenue restricted to monetized states,
    # both aggregated in SQL (never load-all into Python).
    order_count = db.execute(
        select(func.count(Order.id)).where(
            Order.representative_id == rep.id,
            Order.deleted_at.is_(None),
            Order.ordered_at >= start,
        )
    ).scalar_one()
    revenue = float(
        db.execute(
            select(func.coalesce(func.sum(Order.grand_total), 0)).where(
                Order.representative_id == rep.id,
                Order.deleted_at.is_(None),
                Order.ordered_at >= start,
                Order.state.in_(revenue_states),
            )
        ).scalar_one()
    )

    customers = representative_scope_service.resolve_representative_customers(
        db, rep.id,
    )

    summaries = [
        BotReportSummary(label="تعداد سفارشات", value=order_count),
        BotReportSummary(label="درآمد", value=f"{revenue:,.0f}"),
        BotReportSummary(label="تعداد مشتریان", value=len(customers)),
        BotReportSummary(label="دوره", value=label),
    ]

    _audit(
        db,
        entity_type="bot_query",
        entity_id=rep.id,
        action="QUERY",
        rep=rep,
        after={"resource": "reports", "period": period},
    )
    db.commit()

    return BotReportResponse(
        representative_name=rep.person_name,
        period=label,
        summaries=summaries,
    )


# ---------------------------------------------------------------------------
# POST /bot/reps/{rep_id}/invoices
# ---------------------------------------------------------------------------


@router.post(
    "/reps/{rep_id}/invoices",
    response_model=BotInvoiceResponse,
    summary="Create an invoice from an order",
    responses={
        403: {"model": BotErrorResponse},
        409: {"model": BotErrorResponse, "description": "Duplicate invoice / conflict"},
    },
)
def create_rep_invoice(
    rep_id: uuid.UUID,
    body: BotInvoiceCreateRequest,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_write),
    db: Session = Depends(get_db),
) -> BotInvoiceResponse:
    """Create an invoice for a shipped order belonging to the representative.

    ``rep_id`` from the URL is validated against the JWT token; the caller
    must hold ``BOT_WRITE``.  The order is looked up by ``order_number`` and
    must belong to this representative (enforced by the existing service
    layer).  Duplicate invoices for the same order are rejected by the
    invoice service (``InvoiceAlreadyExistsError``).

    Current scope (documented in docs/BOT_SETUP.md): the bot supports the
    ERP workflow ``SHIPPED order -> DRAFT invoice``.  It does NOT create
    orders end-to-end; invoice issuance is performed through the ERP UI
    (``/issue-invoice`` command path in the legacy command architecture).
    """
    from services import invoice_service

    # Audit the attempt before performing the mutation.
    _audit(
        db,
        entity_type="invoice",
        entity_id=rep.id,
        action="ATTEMPT",
        rep=rep,
        after={"order_number": body.order_number},
    )

    # Look up the order by order_number, scoped to the representative.
    try:
        order = order_service.get_order_for_representative_by_number(
            db,
            order_number=body.order_number,
            representative_id=rep.id,
        )
    except order_service.OrderNotFoundError:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{body.order_number}' not found or not accessible.",
        )

    try:
        invoice = invoice_service.create_invoice_from_order(
            db,
            order_id=order.id,
            created_by=_get_system_user_id(db),
        )
    except invoice_service.InvoiceAlreadyExistsError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except invoice_service.OrderNotShippedError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface as 400 to the bot
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invoice creation failed: {exc}",
        ) from exc

    db.commit()

    return BotInvoiceResponse(
        invoice_number=invoice.invoice_number,
        order_number=body.order_number,
        status=invoice.state,
        grand_total=float(invoice.grand_total),
        message="فاکتور با موفقیت صادر شد.",
    )


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/customers
# ---------------------------------------------------------------------------


@router.get(
    "/reps/{rep_id}/customers",
    response_model=BotCustomerListResponse,
    summary="List customers assigned to the representative (ADR-007 scope)",
    responses={403: {"model": BotErrorResponse}},
)
def list_rep_customers(
    rep_id: uuid.UUID,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_query),
    db: Session = Depends(get_db),
) -> BotCustomerListResponse:
    """Return the customers the representative is authorized to sell to.

    Scoped through ``representative_scope_service.resolve_representative_customers``
    (ADR-007) -- never the full customer table.  ``rep_id`` from the URL is
    validated against the JWT by ``require_bot_rep_scope``.
    """
    customers = representative_scope_service.resolve_representative_customers(
        db, rep.id,
    )

    _audit(
        db,
        entity_type="bot_query",
        entity_id=rep.id,
        action="QUERY",
        rep=rep,
        after={"resource": "customers", "items": len(customers)},
    )
    db.commit()

    return BotCustomerListResponse(
        items=[
            BotCustomer(
                id=c.id,
                code=c.code,
                name=c.name,
                currency_id=c.currency_id,
            )
            for c in customers
        ]
    )


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/products
# ---------------------------------------------------------------------------


@router.get(
    "/reps/{rep_id}/products",
    response_model=BotProductListResponse,
    summary="List products available in the representative's primary warehouse",
    responses={403: {"model": BotErrorResponse}},
)
def list_rep_products(
    rep_id: uuid.UUID,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_query),
    db: Session = Depends(get_db),
) -> BotProductListResponse:
    """Return in-stock products of the representative's primary warehouse.

    Uses the existing scope + inventory services only (ADR-007 scope,
    live balances computed from the immutable InventoryTransaction ledger).
    """
    warehouses = representative_scope_service.resolve_representative_warehouses(
        db, rep.id, primary_only=True,
    )
    if not warehouses:
        _audit(
            db,
            entity_type="bot_query",
            entity_id=rep.id,
            action="QUERY",
            rep=rep,
            after={"resource": "products", "warehouse": None},
        )
        db.commit()
        return BotProductListResponse(items=[], warehouse_code="N/A")

    wh = warehouses[0]
    balances = inventory_service.list_warehouse_balances(
        db, warehouse_id=wh.id, limit=100,
    )

    items = [
        BotProduct(
            product_id=b["product_id"],
            sku=b["sku"],
            name=b["name"],
            balance=int(b["balance"]),
        )
        for b in balances
    ]

    _audit(
        db,
        entity_type="bot_query",
        entity_id=rep.id,
        action="QUERY",
        rep=rep,
        after={"resource": "products", "warehouse": wh.code, "items": len(items)},
    )
    db.commit()

    return BotProductListResponse(items=items, warehouse_code=wh.code)


# ---------------------------------------------------------------------------
# GET /bot/reps/{rep_id}/price-preview
# ---------------------------------------------------------------------------


@router.get(
    "/reps/{rep_id}/price-preview",
    response_model=BotPricePreviewResponse,
    summary="Resolve the ERP selling price for a product for a scoped customer",
    responses={
        403: {"model": BotErrorResponse},
        422: {"model": BotErrorResponse, "description": "No price list / no active price"},
    },
)
def price_preview(
    rep_id: uuid.UUID,
    customer_id: uuid.UUID = Query(...),
    product_id: uuid.UUID = Query(...),
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_query),
    db: Session = Depends(get_db),
) -> BotPricePreviewResponse:
    """Return the effective unit price the ERP would apply to an order line.

    This is a read-only resolution over the existing BR-P1 chain
    (``resolve_customer_price_list`` then ``get_current_price``).  The
    caller can never supply or override a price -- no price input exists.
    """
    # The customer must be one the representative is allowed to sell to.
    customers = representative_scope_service.resolve_representative_customers(
        db, rep.id,
    )
    if not any(c.id == customer_id for c in customers):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer is not assigned to this representative.",
        )

    # The product must exist (and be active) -- nothing is resolved for
    # arbitrary/nonexistent products.
    product = db.execute(
        select(Product).where(
            Product.id == product_id, Product.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )

    price_list = price_list_service.resolve_customer_price_list(db, customer_id)
    if price_list is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No price list is assigned to this customer. Assign a price list in the ERP first.",
        )

    entry = price_list_service.get_current_price(
        db, product_id=product_id, price_list_id=price_list.id,
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No currently valid price for this product in the customer's price list.",
        )

    _audit(
        db,
        entity_type="bot_query",
        entity_id=rep.id,
        action="QUERY",
        rep=rep,
        after={
            "resource": "price_preview",
            "customer_id": str(customer_id),
            "product_id": str(product_id),
        },
    )
    db.commit()

    return BotPricePreviewResponse(
        product_id=product.id,
        product_sku=product.sku,
        product_name=product.name,
        unit_price=float(entry.unit_price),
        currency_id=entry.currency_id,
        price_list_id=entry.price_list_id,
        price_type=entry.price_type,
    )


# ---------------------------------------------------------------------------
# POST /bot/reps/{rep_id}/orders
# ---------------------------------------------------------------------------


@router.post(
    "/reps/{rep_id}/orders",
    response_model=BotOrderCreateResponse,
    summary="Create a DRAFT order whose prices are resolved by the ERP",
    responses={
        403: {"model": BotErrorResponse, "description": "Out of scope / missing BOT_WRITE"},
        409: {"model": BotErrorResponse, "description": "Duplicate product on the order"},
        422: {"model": BotErrorResponse, "description": "No price list / no price / product not in warehouse"},
    },
)
def create_rep_order(
    rep_id: uuid.UUID,
    body: BotOrderCreateRequest,
    rep: Representative = Depends(require_bot_rep_scope),
    _rep: Representative = Depends(_require_bot_write),
    db: Session = Depends(get_db),
) -> BotOrderCreateResponse:
    """Create a DRAFT order through the canonical ``order_service.create_order``.

    The representative identity always comes from the JWT (``rep.id`` --
    never the request body).  Lines omit ``price_history_id`` so the ERP
    resolves the unit price from the customer's price list (BR-P1).  The
    order is a DRAFT in the ERP lifecycle: approval/shipment/invoicing
    continue through the ERP, exactly as in the UI.
    """
    _audit(
        db,
        entity_type="order",
        entity_id=rep.id,
        action="ATTEMPT",
        rep=rep,
        after={"customer_id": str(body.customer_id), "lines": len(body.lines)},
    )

    # 1. Customer must be within the representative's scope (ADR-007).
    customers = representative_scope_service.resolve_representative_customers(
        db, rep.id,
    )
    customer = next((c for c in customers if c.id == body.customer_id), None)
    if customer is None:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer is not assigned to this representative.",
        )

    # 2. The representative needs a primary warehouse to fulfill from.
    warehouses = representative_scope_service.resolve_representative_warehouses(
        db, rep.id, primary_only=True,
    )
    if not warehouses:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No warehouse is assigned to this representative.",
        )
    wh = warehouses[0]

    # 3. Every product must be carried by that warehouse (existing inventory
    #    balances -- the same source the bot's product list uses).  A
    #    duplicate product on one order is rejected (ERP rule).
    balances = inventory_service.list_warehouse_balances(
        db, warehouse_id=wh.id, limit=1000,
    )
    available = {b["product_id"]: b for b in balances}

    seen: set[uuid.UUID] = set()
    lines: list[order_service.OrderLineInput] = []
    for line in body.lines:
        if line.product_id in seen:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Product '{line.product_id}' appears more than once on the order.",
            )
        seen.add(line.product_id)
        if line.product_id not in available:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Product is not available in the representative's warehouse.",
            )
        lines.append(
            order_service.OrderLineInput(
                product_id=line.product_id,
                fulfillment_warehouse_id=wh.id,
                qty_ordered=line.qty_ordered,
                fulfillment_mode=body.fulfillment_mode,
            )
        )

    # 4. Create the DRAFT order -- price resolution happens inside
    #    ``order_service.create_order`` (customer price list -> current
    #    price).  The ERP remains the source of truth for all totals.
    try:
        order = order_service.create_order(
            db,
            customer_id=customer.id,
            representative_id=rep.id,
            currency_id=customer.currency_id,
            order_type=body.order_type,
            fulfillment_mode=body.fulfillment_mode,
            sales_channel="BOT_TELEGRAM",
            lines=lines,
            created_by=_get_system_user_id(db),
        )
    except order_service.NoCustomerPriceListError as exc:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except order_service.NoCurrentPriceError as exc:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except order_service.PriceListNotFoundError as exc:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except order_service.PriceListNotActiveError as exc:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except order_service.ProductNotFoundError as exc:
        db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except order_service.CustomerCreditLimitExceededError as exc:
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    # 5. Build the response with ERP-computed line prices.
    order_lines = order_service.list_order_lines(db, order.id)
    product_ids = [ol.product_id for ol in order_lines]
    products = db.execute(
        select(Product).where(Product.id.in_(product_ids))
    ).scalars().all()
    product_map = {p.id: p for p in products}

    line_items = [
        BotOrderLineResponse(
            product_id=ol.product_id,
            product_sku=product_map[ol.product_id].sku,
            product_name=product_map[ol.product_id].name,
            qty_ordered=float(ol.qty_ordered),
            unit_price=float(ol.unit_price),
            line_total=float(ol.line_total),
        )
        for ol in order_lines
    ]

    _audit(
        db,
        entity_type="order",
        entity_id=order.id,
        action="CREATE",
        rep=rep,
        after={
            "order_number": order.order_number,
            "state": order.state,
            "grand_total": float(order.grand_total),
        },
    )
    db.commit()

    return BotOrderCreateResponse(
        order_id=order.id,
        order_number=order.order_number,
        state=order.state,
        subtotal=float(order.subtotal),
        grand_total=float(order.grand_total),
        currency_id=order.currency_id,
        lines=line_items,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_system_user_id(db: Session) -> uuid.UUID:
    """Return the system user ID for audit columns."""
    from sqlalchemy import select
    from database.models.app_user import AppUser
    from services.bootstrap_service import SYSTEM_USERNAME

    user = db.execute(
        select(AppUser.id).where(AppUser.username == SYSTEM_USERNAME)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System user not found.",
        )
    return user


__all__ = ["router"]