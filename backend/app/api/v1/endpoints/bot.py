"""Bot endpoints: ``POST /bot/verify-phone``, ``GET/POST /bot/reps/{rep_id}/...``.

The ``/bot/verify-phone`` endpoint is the entry point for the phone-based
bot authentication flow.  All other ``/bot/`` endpoints require a valid
bot JWT token (via ``require_bot_rep_scope``).

Data endpoints are thin HTTP wrappers over existing service-layer
functions, following the same pattern as every other endpoint in this
backend.  ``rep_id`` is extracted from the JWT token (not from the URL
parameter) to prevent IDOR -- the URL parameter is only used for
readability/documentation.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.bot_auth import get_bot_representative, require_bot_rep_scope
from app.dependencies.db import get_db
from app.schemas.bot import (
    BotErrorResponse,
    BotInvoiceCreateRequest,
    BotInvoiceResponse,
    BotInventoryResponse,
    BotInventoryItem,
    BotReportResponse,
    BotReportSummary,
    BotVerifyPhoneRequest,
    BotVerifyPhoneResponse,
)
from database.models.representative import Representative
from services import bot_phone_service, inventory_service, order_service
from services import representative_scope_service

router = APIRouter(prefix="/bot", tags=["bot"])


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

    On success, returns a short-lived JWT (30 minutes) that the bot
    uses for subsequent API calls.  The token contains the
    ``representative_id`` as its ``sub`` claim.
    """
    settings = get_settings()
    try:
        result = bot_phone_service.verify_phone(
            db,
            phone_number=body.phone_number,
            platform=body.platform.upper(),
            secret_key=settings.secret_key,
        )
    except bot_phone_service.PhoneNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except bot_phone_service.RepresentativeInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except bot_phone_service.InvalidPlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    db.commit()
    return BotVerifyPhoneResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        representative_id=result.representative_id,
        representative_name=result.representative_name,
    )


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
    db: Session = Depends(get_db),
) -> BotInventoryResponse:
    """Return inventory balances for the representative's primary warehouse.

    ``rep_id`` from the URL is validated against the JWT token by
    ``require_bot_rep_scope``.  All data access goes through the
    existing scope service layer (ADR-007).
    """
    warehouses = representative_scope_service.resolve_representative_warehouses(
        db, rep.id, primary_only=True,
    )

    if not warehouses:
        return BotInventoryResponse(items=[], warehouse_code="N/A")

    wh = warehouses[0]
    balances = inventory_service.list_warehouse_balances(
        db, warehouse_id=wh.id, limit=50,
    )

    items = [
        BotInventoryItem(
            sku=b["sku"],
            name=b["name"],
            balance=b["balance"],
            warehouse_code=wh.code,
        )
        for b in balances
    ]

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
    db: Session = Depends(get_db),
) -> BotReportResponse:
    """Return a sales/performance report for the representative.

    Aggregates order count, total revenue, and customer count from the
    existing service layer, scoped to the representative via ADR-007.
    """
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_str = month_start.strftime("%Y-%m")

    # Count orders this month for this representative.
    orders = list(order_service.list_orders(
        db, representative_id=rep.id, limit=1000,
    ))

    # Filter to current month.
    monthly_orders = [
        o for o in orders
        if o.ordered_at and o.ordered_at >= month_start
    ]

    total_revenue = sum(
        float(o.grand_total) for o in monthly_orders
        if o.state in ("SHIPPED", "PAID", "COMPLETED")
    )

    # Count unique customers.
    customers = representative_scope_service.resolve_representative_customers(
        db, rep.id,
    )

    summaries = [
        BotReportSummary(label="تعداد سفارشات", value=len(monthly_orders)),
        BotReportSummary(label="درآمد ماهانه", value=f"{total_revenue:,.0f}"),
        BotReportSummary(label="تعداد مشتریان", value=len(customers)),
        BotReportSummary(label="دوره", value=period_str),
    ]

    return BotReportResponse(
        representative_name=rep.person_name,
        period=period_str,
        summaries=summaries,
    )


# ---------------------------------------------------------------------------
# POST /bot/reps/{rep_id}/invoices
# ---------------------------------------------------------------------------


@router.post(
    "/reps/{rep_id}/invoices",
    response_model=BotInvoiceResponse,
    summary="Create an invoice from an order",
    responses={403: {"model": BotErrorResponse}},
)
def create_rep_invoice(
    rep_id: uuid.UUID,
    body: BotInvoiceCreateRequest,
    rep: Representative = Depends(require_bot_rep_scope),
    db: Session = Depends(get_db),
) -> BotInvoiceResponse:
    """Create an invoice for a shipped order belonging to the representative.

    ``rep_id`` from the URL is validated against the JWT token.
    The order is looked up by ``order_number`` and must belong to this
    representative (enforced by the existing service layer).

    Note: This is a Tier 2 write command (no approval required) because
    invoice creation from a shipped order is a low-risk, naturally bounded
    operation within the representative's scope.
    """
    from services import invoice_service

    # Look up the order by order_number.
    try:
        order = order_service.get_order_for_representative(
            db,
            order_id=_find_order_id_by_number(db, body.order_number),
            representative_id=rep.id,
        )
    except (order_service.OrderNotFoundError, order_service.OrderAccessDeniedError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{body.order_number}' not found or not accessible.",
        )

    try:
        invoice = invoice_service.create_invoice_from_order(
            db,
            order_id=order.id,
            actor_user_id=_get_system_user_id(db),
        )
    except invoice_service.InvoiceCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invoice creation failed: {exc}",
        ) from exc

    db.commit()

    return BotInvoiceResponse(
        invoice_number=invoice.invoice_number,
        order_number=body.order_number,
        status=invoice.status,
        grand_total=float(invoice.grand_total),
        message="فاکتور با موفقیت صادر شد.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_order_id_by_number(db: Session, order_number: str) -> uuid.UUID:
    """Look up an order ID by its order_number string."""
    from sqlalchemy import select
    from database.models.order import Order

    order = db.execute(
        select(Order.id).where(Order.order_number == order_number)
    ).scalar_one_or_none()
    if order is None:
        raise order_service.OrderNotFoundError(order_number)
    return order


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
