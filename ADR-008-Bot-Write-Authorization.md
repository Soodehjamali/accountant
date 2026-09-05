# ADR-008: Bot Write Authorization and Approval Workflow

**Status:** Accepted
**Date:** 2026-08-25
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-007 (Representative Data Scope), ADR-004 (Order State Machine), ADR-006 (Invoice Immutability)

---

## 1. Context

The read-only bot command milestone (Phase A) is complete. Seven commands
are live: `/me`, `/balance`, `/orders`, `/order`, `/inventory`, `/products`,
`/customers`. All require `BOT_QUERY` permission and are scoped to the
session's representative via ADR-007.

Before any write command (creating an order, confirming a transfer, etc.)
can be implemented through the bot, the authorization model must be
defined. Write operations carry real business consequences — inventory
mutations, financial entries, order creation — and must not execute
without proper authorization and, where appropriate, explicit approval.

The existing codebase already contains:
- **RBAC infrastructure**: `Role`, `Permission`, `RolePermission`, `UserRole`
  models with `rbac_service.py` (grant, assign, check).
- **Approval models**: `approval_request` (T25) and `approval_history` (H7)
  — polymorphic, mutable request + append-only history, already migrated.
- **Audit infrastructure**: `audit_log` (H6) + `audit_service.record()`.
- **Bot command framework**: `process_message()` with per-command
  `_required_permission` metadata on handler functions.
- **Bot session service**: binding tokens, session lifecycle, message logging.

This ADR defines how these existing components compose to authorize and
govern Telegram write operations.

## 2. Authorization Model — Three Tiers

### Tier 0: Built-in (no permission check)

Commands that are always available to any bound user:

| Command | Purpose |
|---------|---------|
| `/start` | Welcome message |
| `/help` | List available commands |
| `/link <token>` | Bind Telegram account (admin-generated token) |
| `/unlink` | Info on how to unlink |

These bypass `COMMAND_REGISTRY` entirely — handled as built-in commands
in `process_message()` with no permission gate.

### Tier 1: BOT_QUERY (read-only commands)

Commands that read data scoped to the representative. Current v1 commands:

| Command | Permission | Approval |
|---------|-----------|----------|
| `/me` | `BOT_QUERY` | No |
| `/balance` | `BOT_QUERY` | No |
| `/orders` | `BOT_QUERY` | No |
| `/order <id>` | `BOT_QUERY` | No |
| `/inventory` | `BOT_QUERY` | No |
| `/products` | `BOT_QUERY` | No |
| `/customers` | `BOT_QUERY` | No |

### Tier 2: BOT_WRITE (write commands, no approval)

Commands that mutate state but are low-risk or inherently bounded to
the representative's own scope. These require `BOT_WRITE` permission
but do **not** require an explicit `approval_request`:

| Command (future) | Permission | Approval | Rationale |
|-------------------|-----------|----------|-----------|
| `/confirm <transfer_id>` | `BOT_WRITE` | No | Rep confirming receipt of their own stock; bounded by representative scope. |
| `/set-price <product_id> <price>` | `BOT_WRITE` | No | Price override is explicitly allowed per `04_Business_Policies.md` for the current invoice only. |

The approval_required flag is set per-command at registration time,
not globally on BOT_WRITE.

### Tier 3: BOT_WRITE + approval_required (write commands with approval)

Commands that create significant mutations — new orders, stock adjustments,
returns — requiring explicit approval before execution:

| Command (future) | Permission | Approval | Rationale |
|-------------------|-----------|----------|-----------|
| `/create-order` | `BOT_WRITE` | Yes | Creates a real Order (ADR-004) subject to full business rules. |
| `/adjust <product_id> <delta>` | `BOT_WRITE` | Yes | Stock adjustment per SRS §6.7 BR-A2: requires authorized approver + mandatory reason. |
| `/return <order_id>` | `BOT_WRITE` | Yes | Customer return triggers inventory reversal + commission clawback. |

## 3. Approval Model — Reuse Existing Infrastructure

### What exists (and is reused)

- **`approval_request` (T25)**: Polymorphic via `(entity_type, entity_id)`.
  Status vocabulary: `PENDING` → `APPROVED` / `REJECTED` / `CANCELLED`.
  Separation of duties enforced: `assigned_approver_id IS DISTINCT FROM requested_by`.
  Exactly one `PENDING` request per `(entity_type, entity_id)` at a time
  (partial unique index).

- **`approval_history` (H7)**: Append-only log of every status transition
  on an `approval_request`. No schema changes needed.

- **`audit_log` (H6)**: Every approval grant/reject is an `APPROVE`/`REJECT`
  action on the audit log. Every write mutation that executes after approval
  is a `CREATE`/`UPDATE` action.

### What does NOT exist yet (missing infrastructure)

- **`approval_service.py`**: No service layer for creating, resolving,
  or querying approval requests. This is the primary missing piece.
  The service must:
  - `create_approval_request(session, entity_type, entity_id, requested_by, reason)`
  - `approve_request(session, request_id, approver_id, note)`
  - `reject_request(session, request_id, approver_id, note)`
  - `cancel_request(session, request_id, cancelled_by, note)`
  - `get_pending_request(session, entity_type, entity_id)`
  - `list_pending_requests(session, assigned_approver_id)`

- **Notification dispatch**: When an approval request is created, how are
  approvers notified? No notification dispatch exists. This is explicitly
  out of scope for this milestone — the approval request is created in
  the database; notification delivery is a future concern.

- **Idempotency key framework**: Write commands called via Telegram may
  be retried by the user. A general idempotency mechanism does not exist.
  Out of scope for this milestone; each write command must handle
  idempotency at its own level when implemented.

## 4. Telegram Write Operation Lifecycle

```
Telegram message received
│
├─ Telegram adapter (normalize_update → BotMessage)
│
├─ BotSession resolution (bot_session_service.resolve_session)
│  └─ Raises UnboundSessionError if no linked session
│
├─ AppUser/Representative resolution (identity chain)
│  └─ AppUser looked up by representative_id FK (not PK)
│
├─ RBAC check (user_has_permission)
│  ├─ Tier 0: skip (built-in commands)
│  ├─ Tier 1: check BOT_QUERY
│  └─ Tier 2/3: check BOT_WRITE
│     └─ Raises PermissionDeniedError if missing
│
├─ Command dispatch
│  │
│  ├─ [No approval required]
│  │  ├─ Validate command arguments
│  │  ├─ Enforce representative scope (ADR-007)
│  │  ├─ Execute mutation (domain service call)
│  │  ├─ Audit log entry (audit_service.record)
│  │  └─ Return BotResponse
│  │
│  └─ [Approval required]
│     ├─ Validate command arguments
│     ├─ Enforce representative scope (ADR-007)
│     ├─ Create approval_request (PENDING)
│     │  └─ approval_service.create_approval_request()
│     ├─ [Future: Notify approvers via notification_service]
│     ├─ Audit log entry (audit_service.record, action=CREATE)
│     ├─ Return BotResponse: "Your request is pending approval."
│     │
│     └─ [Later: Approver acts via web UI or admin bot]
│        ├─ approval_service.approve_request()
│        ├─ Audit log entry (action=APPROVE)
│        ├─ Execute mutation (domain service call)
│        ├─ Audit log entry (action=CREATE/UPDATE)
│        └─ [Future: Notify requester of approval + result]
```

## 5. Command Registration — Extended Metadata

The existing `_register_command` decorator is extended with two new
parameters (backward-compatible — defaults preserve current behavior):

```python
@_register_command(
    "create-order",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=True,    # NEW
)
def handle_create_order(session, user, rep, args):
    ...
```

- `approval_required=False` (default): command executes immediately.
- `approval_required=True`: command creates an `approval_request` first;
  the actual mutation is deferred until approval.

The `process_message()` function reads `approval_required` from the
handler's metadata and routes accordingly.

## 6. Invariant Preservation

**Invariant**: No tool/action with `approval_required=True` may execute
without a matching `approval.granted` event.

Enforcement points:
1. **Command handler registration**: `approval_required=True` is declared
   at registration time, not at runtime.
2. **process_message() dispatch**: When `approval_required=True`, the
   handler is NOT called directly. Instead, an `approval_request` is
   created and the handler is deferred.
3. **Approval service**: `approve_request()` writes an `approval_history`
   row with `to_status='APPROVED'` — this is the "approval.granted event".
4. **Deferred execution**: The mutation handler is only called after
   `approve_request()` succeeds. The approval request ID is stored as
   the entity's `approval_request_id` for traceability.

## 7. Scope Enforcement for Write Commands

Every write command must enforce the same representative scope as read
commands (ADR-007):

1. The command handler receives the `representative` from `process_message()`.
2. The handler calls the domain service with `representative_id=rep.id`.
3. The domain service enforces cross-representative access prohibition.
4. The bot adapter and command handler never bypass the scope service.

## 8. Files Changed in This Milestone

| File | Change | Purpose |
|------|--------|---------|
| `ADR-008-Bot-Write-Authorization.md` | **New** | This ADR |
| `services/approval_service.py` | **New** | Approval request CRUD + resolve |
| `services/bot_command_service.py` | Modified | Add `approval_required` metadata to `_register_command`; update `process_message()` to route through approval for write commands |
| `services/bootstrap_service.py` | Modified | Seed `BOT_WRITE` permission (NOT granted to ADMIN by default) |
| `backend/tests/test_bot_write_authorization.py` | **New** | Architecture-level tests for the authorization design |

## 9. Schema Changes

**None.** The existing `approval_request` (T25) and `approval_history` (H7)
tables are sufficient. No new columns, tables, or constraints are needed.

## 10. What This Milestone Does NOT Implement

- No actual write commands (`/create-order`, `/adjust`, etc.)
- No notification dispatch for approval requests
- No idempotency key framework
- No approval UI (web or bot-based approver interface)
- No `BOT_WRITE` permission granted to any role by default
- No modification to the database schema

## 11. Unresolved Architectural Decisions

1. **Approver selection**: When an approval request is created, who is
   `assigned_approver_id`? Options: (a) a designated admin/manager per
   representative, (b) any user with `ORDER_APPROVE` (or similar) permission,
   (c) a configurable approval chain. Deferred to implementation.

2. **Approval timeout**: What happens to a PENDING approval request that
   is never acted on? No timeout policy exists. Deferred.

3. **Approval cancellation by requester**: Can the Telegram user who
   created the request cancel it before approval? The `approval_request`
   model supports CANCELLED status, but no cancel-by-requester policy
   exists. Deferred.

4. **Bulk operations**: If a write command creates multiple entities
   (e.g., a multi-line order), is one approval_request per order or per
   line? Recommended: one per order (entity_type='order'), not per line.
   Deferred.

---

*Generated with Codebuff*
