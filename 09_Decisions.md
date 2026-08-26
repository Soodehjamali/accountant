## ADR-009

Approval Workflow Policies — Approver Selection, Timeout, Cancellation, Bulk Operations.

Status: Accepted
Date: 2026-08-26

See ADR-009-Approval-Workflow-Policies.md for the full decision record.

Summary of decisions:

1. **Approver selection:** Any user with command-specific approval permission
   (e.g. ORDER_APPROVE). assigned_approver_id remains NULL at creation time;
   the queue is list_pending_requests().

2. **Approval timeout:** Leave PENDING indefinitely. No scheduler/worker
   infrastructure exists; auto-cancellation of financial operations without
   human review is risky. Future enhancement when scheduler is built.

3. **Requester cancellation:** Allowed while PENDING. The requester may
   cancel their own request. Separation of duties is preserved (cancel ≠ approve).

4. **Bulk operations:** One approval_request per batch (all-or-nothing).
   Payload carries all data; executor processes all entities atomically.

Schema changes: NONE (existing model supports all decisions).
Production code changes: NONE (decisions define policies, not new code).

---

## ADR-007

Representative Data Scope authorization pattern.

Every consumer that reads data on behalf of a bound representative
(bot commands, API endpoints, reporting, future representative
portal) must enforce the same scope rules through a single shared
service layer: ``services/representative_scope_service.py``.

Scope resolution:

1. **Representative → Customer scope** is resolved through
   ``customer_rep_assignment`` (C6). A customer is "assigned to" a
   representative when the assignment row's ``effective_from <= at``
   AND (``effective_to IS NULL`` OR ``effective_to > at``), where
   ``at`` defaults to ``datetime.now(timezone.utc)``. Multiple
   simultaneously-effective assignments for the same customer are
   ranked by ``priority`` (ascending = highest priority first).

2. **Representative → Warehouse scope** is resolved through
   ``warehouse_assignment`` (C5). A warehouse is "assigned to" a
   representative when the assignment row's ``effective_from <= at``
   AND (``effective_to IS NULL`` OR ``effective_to > at``). The
   ``is_primary`` flag identifies the representative's primary
   warehouse; callers may request ``primary_only=True`` to retrieve
   only the primary warehouse.

3. **Order authorization** is enforced at the service layer:
   ``order_service.get_order_for_representative()`` fetches an order
   by ID and rejects access when
   ``order.representative_id != requested_representative_id``. This
   prevents cross-representative data leakage through direct ID
   access.

4. **Cross-representative access prohibition**: No scope function
   returns data belonging to a different representative. Every query
   is anchored to the representative's own assignment rows. The bot
   session's ``representative_id`` (from ``bot_session`` M12) is the
   sole identity anchor.

5. **Scope enforcement location**: The scope service lives in the
   domain/service layer, NOT in the Telegram adapter, NOT in bot
   command handlers, and NOT in any platform-specific code. All
   consumers (bot, API, reporting, future portal) call the same
   functions.

6. **Scope functions do NOT duplicate assignment or business rules**.
   They read through the existing ``customer_rep_assignment`` and
   ``warehouse_assignment`` tables, respecting their time-window
   semantics and priority ordering. No new constraints, triggers, or
   columns are added to these tables for scoping purposes.

Scope functions added in this milestone:

- ``resolve_representative_customers(session, representative_id, at=None)``
- ``resolve_representative_warehouses(session, representative_id, at=None, primary_only=False)``
- ``order_service.get_order_for_representative(session, order_id, representative_id)``

Out of scope for this ADR (deferred to future decisions):

- Balance command semantics (how to present AR balance per customer)
- Inventory command semantics (which products, which warehouse)
- Customer selection UX (single vs. list, filtering)
- Warehouse selection rules (primary vs. all vs. ask)
- Write operations (BOT_WRITE permission not implemented)

Reason:

The ERD's ``bot_session`` (M12) business constraints state "commands
scoped by this binding; no cross-rep access." SRS §15.5 states "Keep
bot adapters thin; identity resolution centralized and scoped." The
domain model already defines the assignment tables (C5, C6) but no
service-layer resolution existed. This ADR fills that gap with a
single, reusable scope layer.

Status:

Accepted
