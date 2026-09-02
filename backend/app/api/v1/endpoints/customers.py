"""Customer endpoints: ``POST/GET /customers``, ``GET/PATCH /customers/{id}``,
``POST /customers/{id}/deactivate``.

REWRITTEN from the version found in the uploaded archive. That version
could not run against this codebase at all:

* It imported ``app.db.session``, ``app.models.customer``,
  ``app.models.order``, ``app.models.invoice`` -- none of these modules
  exist here. The real DB session dependency is
  ``app.dependencies.db.get_db`` and the real ORM models live under
  ``database.models.*``.
* It imported ``app.dependencies.rbac.PermissionChecker``, which does not
  exist -- this project's actual RBAC dependency is the
  ``require_permission(code)`` factory in ``app/dependencies/rbac.py``
  (see ``endpoints/rbac.py`` for the established usage pattern).
* It assumed an integer ``customer_id`` / boolean ``is_active`` / ad-hoc
  ``phone``, ``national_id`` fields -- the real ``Customer`` model (M8)
  has a UUID PK, a ``status`` string (``ACTIVE``/``INACTIVE``), and no
  ``phone``/``national_id`` columns at all.
* It embedded all query/mutation logic directly in the endpoint
  functions, which conflicts with this project's own layering rule
  (``services/__init__.py`` and ``CLAUDE.md``'s DDD rule) -- business
  logic belongs in ``services/``, endpoints stay thin wrappers.
* The ``GET /{customer_id}/credit-status`` handler referenced
  ``app.models.order`` / ``app.models.invoice`` and used ``Decimal``
  without importing it -- both domains (Sales/Order, Finance/Invoicing)
  do not exist in this codebase yet, so that endpoint could not have
  worked and is intentionally not carried over. It should be
  reintroduced once Order/Invoice actually exist.

This version wraps ``services.customer_service`` only, per the pattern in
``endpoints/products.py`` / ``endpoints/rbac.py``. Mutating endpoints are
gated behind the ``CUSTOMER_MANAGE`` permission (via ``require_permission``,
mirroring how ``endpoints/rbac.py`` gates behind ``RBAC_MANAGE``); reads
require only an authenticated caller, matching the "authenticated only for
now" convention ``endpoints/products.py`` and ``endpoints/inventory.py``
both already document for endpoints RBAC hasn't been asked to narrow yet.
``CUSTOMER_MANAGE`` is not auto-seeded -- an RBAC admin (holding
``RBAC_MANAGE``) must create it and grant it via the existing
``/api/v1/rbac`` endpoints before any non-bootstrap user can write here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_customer_scope, require_permission
from app.schemas.customer import (
    CustomerCreateRequest,
    CustomerListResponse,
    CustomerPriceListAssignRequest,
    CustomerPriceListListResponse,
    CustomerPriceListResponse,
    CustomerResponse,
    CustomerUpdateRequest,
)
from database.models.app_user import AppUser
from services import customer_service, price_list_service

router = APIRouter(prefix="/customers", tags=["customers"])

CUSTOMER_MANAGE_PERMISSION_CODE = "CUSTOMER_MANAGE"
_require_customer_manage = require_permission(CUSTOMER_MANAGE_PERMISSION_CODE)


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer",
)
def create_customer(
    body: CustomerCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> CustomerResponse:
    try:
        customer = customer_service.create_customer(
            db,
            code=body.code,
            name=body.name,
            type=body.type.value,
            currency_id=body.currency_id,
            city_ref_id=body.city_ref_id,
            billing_address=body.billing_address,
            credit_limit_amount=body.credit_limit_amount,
            tax_number=body.tax_number,
            created_by=current_user.id,
        )
    except customer_service.DuplicateCustomerCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=CustomerListResponse, summary="List customers")
def list_customers(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(default=None, description="Matches name, code, or tax number"),
    status_: str | None = Query(default=None, alias="status"),
) -> CustomerListResponse:
    # Server-side representative scope: representative-linked users
    # can only see customers assigned to their representative.  Admin/staff
    # users (no representative link) see all customers.
    representative_id = (
        current_user.representative_id
        if current_user.representative_id is not None
        else None
    )
    items = customer_service.list_customers(
        db, search=search, status=status_,
        representative_id=representative_id, skip=skip, limit=limit,
    )
    return CustomerListResponse(items=list(items))


@router.get("/{customer_id}", response_model=CustomerResponse, summary="Get a customer")
def read_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> CustomerResponse:
    # Customer scope: verify representative is assigned to this customer
    # BEFORE returning any data.  404 for out-of-scope (not 403) to prevent
    # existence leakage, matching the convention of order_scope/
    # invoice_scope/transfer_scope.
    _require_customer_scope(customer_id, current_user, db)

    try:
        return customer_service.get_customer(db, customer_id)
    except customer_service.CustomerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{customer_id}", response_model=CustomerResponse, summary="Update a customer")
def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> CustomerResponse:
    # Customer scope: verify representative is assigned to this customer
    # BEFORE allowing any mutation.
    _require_customer_scope(customer_id, current_user, db)

    try:
        customer = customer_service.update_customer(
            db,
            customer_id,
            updated_by=current_user.id,
            name=body.name,
            city_ref_id=body.city_ref_id,
            billing_address=body.billing_address,
            credit_limit_amount=body.credit_limit_amount,
            tax_number=body.tax_number,
            status=body.status.value if body.status is not None else None,
        )
    except customer_service.CustomerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(customer)
    return customer


@router.post(
    "/{customer_id}/deactivate",
    response_model=CustomerResponse,
    summary="Deactivate a customer (status -> INACTIVE; never a hard delete)",
)
def deactivate_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> CustomerResponse:
    # Customer scope: verify representative is assigned to this customer
    # BEFORE allowing deactivation.
    _require_customer_scope(customer_id, current_user, db)

    try:
        customer = customer_service.deactivate_customer(
            db, customer_id, updated_by=current_user.id
        )
    except customer_service.CustomerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(customer)
    return customer


@router.post(
    "/{customer_id}/reactivate",
    response_model=CustomerResponse,
    summary="Reactivate a customer (status -> ACTIVE)",
)
def reactivate_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> CustomerResponse:
    # Customer scope: verify representative is assigned to this customer
    # BEFORE allowing reactivation.
    _require_customer_scope(customer_id, current_user, db)

    try:
        customer = customer_service.reactivate_customer(
            db, customer_id, updated_by=current_user.id
        )
    except customer_service.CustomerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except customer_service.CustomerAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(customer)
    return customer


# -----------------------------------------------------------------------
# Customer Price List Assignments (BR-P1)
# -----------------------------------------------------------------------


@router.get(
    "/{customer_id}/price-lists",
    response_model=CustomerPriceListListResponse,
    summary="List price-list assignments for a customer",
)
def list_customer_price_lists(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CustomerPriceListListResponse:
    items = price_list_service.list_customer_price_lists(db, customer_id)
    return CustomerPriceListListResponse(items=list(items))


@router.post(
    "/{customer_id}/price-lists",
    response_model=CustomerPriceListResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a price list to a customer",
)
def assign_customer_price_list(
    customer_id: uuid.UUID,
    body: CustomerPriceListAssignRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> CustomerPriceListResponse:
    try:
        assignment = price_list_service.assign_customer_price_list(
            db,
            customer_id=customer_id,
            price_list_id=body.price_list_id,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            priority=body.priority,
            created_by=current_user.id,
        )
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except price_list_service.PriceListNotActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(assignment)
    return assignment


@router.delete(
    "/{customer_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a customer",
)
def delete_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_customer_manage),
) -> None:
    """Hard-delete a customer if it is not referenced by any other records.

    Returns HTTP 409 if the customer is still in use.
    """
    try:
        customer_service.delete_customer(db, customer_id)
    except customer_service.CustomerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except customer_service.CustomerInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()


__all__ = ["router"]
