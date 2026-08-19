"""Service layer for the Enterprise ERP (SIWRMS).

Architecture note (Phase 1 — desktop-first):

Every function in this package takes an already-open SQLAlchemy ``Session``
as its first argument and never opens/commits/closes a session itself.
Callers (a desktop UI, a CLI script, or — later — a FastAPI endpoint) own the
session lifecycle via ``database.session.get_session()``.

This is deliberate: it is what lets the *same* service functions be reused
unchanged by both the Phase 1 desktop app and a Phase 2 web API, instead of
duplicating business logic in two places. No business rule (SKU uniqueness,
status vocabularies, immutability, etc.) should ever be enforced only in a
UI layer or only in an API layer -- it belongs here.
"""

from __future__ import annotations

__all__: list[str] = []
