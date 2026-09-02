"""Minimal currency endpoint: ``GET /currencies/default``.

Returns the default (base) currency so the frontend can obtain the real
UUID instead of hardcoding a placeholder.  This is NOT a currency-management
feature — it is a single read-only lookup used by inventory and order forms.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.schemas.currency import CurrencyResponse
from database.models.app_user import AppUser
from database.models.currency import Currency

router = APIRouter(prefix="/currencies", tags=["currencies"])


@router.get(
    "/default",
    response_model=CurrencyResponse,
    summary="Get the default (base) currency",
)
def get_default_currency(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CurrencyResponse:
    """Return the single base currency (``is_base = true``).

    The bootstrap seeds IRR as the default base currency.  If no base
    currency exists (should not happen in a running system), returns 404.
    """
    currency = db.execute(
        select(Currency).where(Currency.is_base == True)
    ).scalar_one_or_none()

    if currency is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No default currency configured.",
        )

    return CurrencyResponse.model_validate(currency)


__all__ = ["router"]
