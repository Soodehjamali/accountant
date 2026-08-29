"""Credit Note endpoints: ``/api/v1/credit-notes``.

Thin HTTP wrappers around ``services.credit_note_service`` -- business rules
live there, per this project's layering rule.  Every mutating endpoint is
gated behind ``CREDIT_NOTE_MANAGE`` via ``require_permission``; reads require
only an authenticated caller, matching the convention every other domain
endpoint in this codebase documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_credit_note_scope, _require_invoice_scope, require_permission
from app.schemas.credit_notes import (
    CreditNoteCreateRequest,
    CreditNoteLineResponse,
    CreditNoteListResponse,
    CreditNoteResponse,
    CreditNoteTransitionRequest,
)
from database.models.app_user import AppUser
from services import credit_note_service, customer_ledger_service

router = APIRouter(prefix="/credit-notes", tags=["credit-notes"])

_require_credit_note_manage = require_permission(
    credit_note_service.CREDIT_NOTE_MANAGE_PERMISSION_CODE
)

#: Map service-layer exceptions to the HTTP status they should surface.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (credit_note_service.CreditNoteNotFoundError, status.HTTP_404_NOT_FOUND),
    (credit_note_service.InvalidCreditNoteStateTransitionError, status.HTTP_409_CONFLICT),
    (credit_note_service.CreditNoteImmutableError, status.HTTP_409_CONFLICT),
    (credit_note_service.InvoiceNotCreditableError, status.HTTP_409_CONFLICT),
    (credit_note_service.CreditNoteLineQtyNonPositiveError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (credit_note_service.CreditNoteAmountNonPositiveError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)


def _run(func, /, *args, **kwargs):
    """Call a credit_note_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(credit_note, lines=None) -> CreditNoteResponse:
    response = CreditNoteResponse.model_validate(credit_note)
    if lines is not None:
        response.lines = [CreditNoteLineResponse.model_validate(line) for line in lines]
    return response


@router.post(
    "",
    response_model=CreditNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft credit note against an invoice",
)
def create_credit_note(
    body: CreditNoteCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_credit_note_manage),
) -> CreditNoteResponse:
    # Invoice scope: verify the referenced invoice belongs to the caller's
    # representative (or that the caller is admin/staff).  Must happen
    # before credit note creation to prevent side effects.
    _require_invoice_scope(body.invoice_id, current_user, db)
    credit_note = _run(
        credit_note_service.create_credit_note,
        db,
        invoice_id=body.invoice_id,
        reason_code_id=body.reason_code_id,
        lines=[line.model_dump() for line in body.lines],
        created_by=current_user.id,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
        note=body.note,
    )
    db.commit()
    db.refresh(credit_note)
    lines = credit_note_service.list_credit_note_lines(db, credit_note.id)
    return _to_response(credit_note, lines)


@router.get(
    "/{credit_note_id}",
    response_model=CreditNoteResponse,
    summary="Get a credit note and its lines",
)
def read_credit_note(
    credit_note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> CreditNoteResponse:
    # Credit note scope: verify the credit note's linked invoice belongs
    # to the caller's representative.
    _require_credit_note_scope(credit_note_id, current_user, db)
    credit_note = _run(credit_note_service.get_credit_note, db, credit_note_id)
    lines = credit_note_service.list_credit_note_lines(db, credit_note.id)
    return _to_response(credit_note, lines)


@router.post(
    "/{credit_note_id}/issue",
    response_model=CreditNoteResponse,
    summary="DRAFT -> ISSUED",
)
def issue_credit_note(
    credit_note_id: uuid.UUID,
    body: CreditNoteTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_credit_note_manage),
) -> CreditNoteResponse:
    _require_credit_note_scope(credit_note_id, current_user, db)
    credit_note = _run(
        credit_note_service.issue_credit_note,
        db,
        credit_note_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(credit_note)
    return _to_response(credit_note)


@router.post(
    "/{credit_note_id}/apply",
    response_model=CreditNoteResponse,
    summary="ISSUED -> APPLIED (marks invoice CLOSED_CORRECTED)",
)
def apply_credit_note(
    credit_note_id: uuid.UUID,
    body: CreditNoteTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_credit_note_manage),
) -> CreditNoteResponse:
    _require_credit_note_scope(credit_note_id, current_user, db)
    # Validate state BEFORE calling the service, so we get a 409
    # (not 501) when the credit note isn't in ISSUED state.
    cn = _run(credit_note_service.get_credit_note, db, credit_note_id)
    if cn.state != "ISSUED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=str(
                credit_note_service.InvalidCreditNoteStateTransitionError(
                    cn.state, "APPLIED"
                )
            ),
        )
    try:
        credit_note = credit_note_service.apply_credit_note(
            db,
            credit_note_id,
            actor_user_id=current_user.id,
            record_entry=customer_ledger_service.record_entry,
            note=body.note,
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise
    db.commit()
    db.refresh(credit_note)
    return _to_response(credit_note)


@router.post(
    "/{credit_note_id}/void",
    response_model=CreditNoteResponse,
    summary="DRAFT -> VOID",
)
def void_credit_note(
    credit_note_id: uuid.UUID,
    body: CreditNoteTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_credit_note_manage),
) -> CreditNoteResponse:
    _require_credit_note_scope(credit_note_id, current_user, db)
    credit_note = _run(
        credit_note_service.void_credit_note,
        db,
        credit_note_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(credit_note)
    return _to_response(credit_note)


__all__ = ["router"]
