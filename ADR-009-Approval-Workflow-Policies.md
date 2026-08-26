# ADR-009: Approval Workflow Policies

**Status:** Accepted
**Date:** 2026-08-26
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-008 (Bot Write Authorization and Approval Workflow)

---

## 1. Context

ADR-008 established the approval-gated write authorization model: three
tiers of bot commands, `approval_request` / `approval_history` /
`audit_log` reuse, and the `approval_service` / `approval_execution_service`
infrastructure. Four remaining architectural policies were deferred to
implementation:

1. **Approver selection** — When an approval request is created, who is
   `assigned_approver_id`?
2. **Approval timeout** — What happens to a PENDING request that is never
   acted on?
3. **Requester cancellation** — Can the Telegram user who created the
   request cancel it before approval?
4. **Bulk operation approval semantics** — When a command creates multiple
   entities, how many approval requests are needed?

This ADR resolves all four.

---

## 2. Decision 1: Approver Selection

### Problem

When `approval_service.create_approval_request()` is called, the
`assigned_approver_id` field determines who may resolve the request. No
policy currently exists for populating this field.

### Considered Options

**Option A: Designated approver/manager per representative**
A new column or table maps each Representative to a default approver
(AppUser). When a request is created, the system auto-assigns the
representative's designated manager.

**Option B: Any user with a dedicated approval permission (e.g. ORDER_APPROVE)**
The `assigned_approver_id` is left NULL at creation time. Any user who
holds the `ORDER_APPROVE` permission (or command-specific equivalent)
can resolve it via a queue or API endpoint.

**Option C: Configurable approval chain**
A multi-step escalation chain per entity type or per representative.
An approval request moves through a sequence of approvers.

### Evaluation

| Criterion | Option A | Option B | Option C |
|-----------|----------|----------|----------|
| Security | Good — explicit mapping | Good — permission-based | Complex to secure |
| Least privilege | Tight — only one person can approve | Loose — anyone with permission | Depends on chain config |
| Implementation complexity | Low (one FK column) | Low (already have permission infra) | High — chain model, sequence tracking |
| Scalability | Poor — bottleneck on single manager | Good — distributed workload | Good but over-engineered |
| Auditability | Clear — one known approver | Clear — whoever resolved it | Clear but complex |
| Future portal/API compatibility | Compatible | Compatible | Compatible |
| Representative isolation | Neutral | Neutral | May cross boundaries |
| Administrative usability | Simple — assign managers | Simple — assign permissions | Complex — maintain chains |

### Recommendation: Option B

**Rationale:**

The existing RBAC infrastructure already supports this pattern perfectly.
The `permission` model, `role_permission` grants, and
`rbac_service.user_has_permission()` check are production-tested. A new
permission code like `ORDER_APPROVE` (already seeded in
`bootstrap_service.py` per the roadmap) provides the authorization gate.

Key design points:
- `assigned_approver_id` remains NULL at creation time. This is already
  supported by the model (`nullable=True`) and the service.
- The resolution queue is `list_pending_requests()` — it returns all
  PENDING requests, optionally filtered by a specific approver ID.
- Authorization for resolution is enforced by the caller (API endpoint or
  future admin bot command), NOT by the approval service itself. This
  matches the existing pattern: `approval_service.py` docstring states
  "This module does NOT check permissions."
- Command-specific approval permissions (e.g., `ORDER_APPROVE`,
  `ADJUST_APPROVE`, `RETURN_APPROVE`) are introduced per command as
  they are implemented. The current `ORDER_APPROVE` permission is
  already seeded.

**Schema compatibility:** The existing `approval_request` model already
supports this approach. `assigned_approver_id` is nullable and indexed
(partial index for PENDING). No schema changes are required.

**Explicitly deferred:** A future portal or admin bot command that
presents a "pending approval queue" and allows an authorized user to
resolve requests. The data model is ready; the UI/UX is out of scope.

---

## 3. Decision 2: Approval Timeout

### Problem

No policy exists for PENDING approval requests that are never acted on.
Over time, stale requests accumulate and may create confusion or
operational issues.

### Considered Options

**Option A: Leave PENDING indefinitely**
No timeout. Requests remain PENDING until someone acts on them.

**Option B: Auto-cancel after N days**
A background job scans for PENDING requests older than N days and
transitions them to CANCELLED.

**Option C: Escalate after N days**
A background job escalates (reassigns to a higher-level approver) after
N days, then auto-cancels after M days.

**Option D: Configurable timeout per entity type**
Different entity types have different timeout periods, stored in a
configuration table.

### Evaluation

| Criterion | Option A | Option B | Option C | Option D |
|-----------|----------|----------|----------|----------|
| Audit requirements | Acceptable — PENDING is auditable | Good — explicit cancellation is auditable | Good — escalation is auditable | Good |
| Stale approvals | Problematic — indefinite PENDING | Resolved — auto-cancel | Resolved — escalate then cancel | Resolved |
| Operational simplicity | Simplest | Simple | Complex | Moderate |
| Background job required | No | Yes | Yes | Yes |
| Scheduler infrastructure | Not needed | Needed — does not exist yet | Needed — does not exist yet | Needed |
| Existing timestamp fields | `requested_at` is available | `requested_at` is available | `requested_at` is available | `requested_at` is available |
| Financial/accounting safety | Low risk — PENDING never executes | Low risk — explicit cancellation | Medium risk — wrong escalation path | Low risk |

### Recommendation: Option A (Leave PENDING indefinitely) — with a documented future enhancement

**Rationale:**

1. **No scheduler/worker infrastructure exists.** The project has no
   background job system (confirmed by `bootstrap_service.py` and
   `report_service.py` docstrings). Building one solely for approval
   timeouts is disproportionate to the current maturity level.

2. **Financial/accounting safety.** In a financial system, auto-cancelling
   an approval request without human review is risky. A PENDING request
   for a stock adjustment might be intentional — the approver may be
   waiting for additional information before deciding. Auto-cancellation
   could silently discard a legitimate request.

3. **Operational simplicity.** At the current scale (single company,
   Telegram bot), manual management of PENDING requests is tractable.
   The `list_pending_requests()` function provides the queue view.

4. **Append-only audit trail.** The `approval_history` table records
   every state transition. A future timeout job can add a
   `CANCELLED` transition with a `note='Auto-cancelled after N days'`
   when the scheduler infrastructure is built.

**Documented future enhancement:** When a scheduler/worker system is
introduced, implement a periodic job that:
- Flags PENDING requests older than 7 days in a dashboard/notification.
- Auto-cancels PENDING requests older than 30 days with a documented
  reason ("Auto-cancelled: approval timeout").
- Logs the auto-cancellation in `approval_history` with
  `actor_user_id=NULL` (system-initiated).

**Explicitly deferred:** Background job, scheduler infrastructure,
timeout configuration. The `requested_at` timestamp on `approval_request`
is already sufficient for a future timeout query.

---

## 4. Decision 3: Requester Cancellation

### Problem

The `cancel_request()` function in `approval_service.py` already supports
cancellation of PENDING requests and records it in `approval_history` and
`audit_log`. However, no policy defines who may cancel a request.

### Considered Options

**Option A: Allow requester cancellation while PENDING**
The Telegram user who created the request (identified by `requested_by`)
may cancel it before resolution.

**Option B: Allow only approver/admin cancellation**
Only the assigned approver or a user with an admin permission may cancel.

**Option C: Do not support cancellation**
Remove the cancel capability entirely.

### Evaluation

| Criterion | Option A | Option B | Option C |
|-----------|----------|----------|----------|
| Separation of duties | Preserved — cancel ≠ approve | Preserved | N/A |
| Auditability | Good — requester action is recorded | Good | Poor — no way to clean up |
| Accidental cancellation | Risk — user might cancel by mistake | Lower risk | No risk |
| Stale requests | Resolved — requester can clean up | Requires approver action | Unresolved |
| Authorization | Clear — requester identity from BotSession | Clear | N/A |
| Interaction with optimistic locking | Compatible — same as approve/reject | Compatible | N/A |
| Interaction with terminal states | Already enforced — only PENDING can be cancelled | Same | N/A |

### Recommendation: Option A — Allow requester cancellation while PENDING

**Rationale:**

1. **Existing code already supports it.** `cancel_request()` is
   implemented, tested, and records audit/history correctly. Removing it
   would be a regression.

2. **Separation of Duties preserved.** Cancellation is NOT approval.
   The requester is undoing their own request, not resolving someone
   else's. The separation-of-duties check (approver ≠ requester) on
   `approve_request()` and `reject_request()` is unaffected.

3. **Operational necessity.** A Telegram user who accidentally submits
   `/create-order` with wrong parameters should be able to cancel the
   pending request rather than waiting for an admin to reject it.

4. **Concurrency safety.** `cancel_request()` uses the same transition
   guard (only PENDING → CANCELLED) and optimistic locking as approve
   and reject. Concurrent cancel vs. approve is already tested in
   `test_approval_concurrency.py`.

**State transition:**
```
PENDING → CANCELLED
  Actor: requester (requested_by) or any user with admin permission
  Guard: status == 'PENDING'
```

**Who may cancel:**
- The requester (identified by `requested_by` from the BotSession identity chain).
- Any user with an admin-level permission (future: `APPROVAL_MANAGE`).

**Explicitly deferred:** An admin-facing cancellation command or API
endpoint. The data model and service layer are ready; the interface is
out of scope for this phase.

---

## 5. Decision 4: Bulk Operation Approval Semantics

### Problem

When a future write command creates multiple entities (e.g., a multi-line
order, a batch stock adjustment), how should approval requests be
structured?

### Considered Options

**Option A: One approval_request per entity**
Each individual entity gets its own approval request. A 5-line order
creates 5 approval requests.

**Option B: One approval_request for the entire batch**
A single approval request covers the whole operation. All-or-nothing.

**Option C: Parent approval + child execution records**
A parent approval request, with child records tracking individual
entity execution.

### Evaluation

| Criterion | Option A | Option B | Option C |
|-----------|----------|----------|----------|
| Atomicity | Partial — each entity is independent | All-or-nothing | All-or-nothing with tracking |
| Auditability | Fine-grained — per entity | Coarse — one record | Best — both levels |
| Partial failure | Natural — some succeed, some don't | Problematic — what if line 3 of 5 fails? | Complex to implement |
| Retry behavior | Retry individual entities | Retry entire batch | Retry individual children |
| User experience | Confusing — "5 approvals pending" | Simple — one approval | Complex |
| Approval semantics | Approver must review each line | Approver reviews entire batch | Both levels |
| Future reporting | Complex — must aggregate | Simple — one row | Complex — parent/child |
| Database complexity | Low — standard model | Low — standard model | High — requires new table or self-referential FK |

### Recommendation: Option B — One approval_request for the entire batch

**Rationale:**

1. **Simplicity.** The current `approval_request` model with its
   `(entity_type, entity_id)` polymorphic pattern already supports this.
   For a batch operation, `entity_type` identifies the operation type
   (e.g., `bot_command:create-order`) and `entity_id` identifies the
   batch (e.g., the bot session ID or a batch UUID).

2. **All-or-nothing semantics.** Financial operations should be atomic.
   A multi-line order where lines 1-3 are approved but lines 4-5 are
   rejected creates an inconsistent state. One approval request ensures
   the entire batch is approved or rejected together.

3. **Existing pattern.** The `/create-order` command already follows
   this pattern — one approval request per order, not per order line.
   The payload contains all lines. This is the established convention.

4. **Existing unique constraint.** The partial unique index
   `uq_approval_request_one_pending` on `(entity_type, entity_id)` where
   `status = 'PENDING'` naturally enforces one pending request per batch.

5. **Audit trail.** The `approval_history` table records the batch-level
   transition. Individual entity execution results are captured in the
   `audit_log` (H6) by the executor function.

**For the current project:** `/create-order` creates a single order (one
entity), so bulk semantics are not yet exercised. The pattern is
established by the payload mechanism: all data for the operation is
serialized into the `payload` JSON column.

**Explicitly deferred:** A batch-specific entity type (e.g.,
`bot_command:batch-adjust`) or a batch tracking table. When bulk
operations are introduced, the payload carries the full batch data and
the executor processes all entities within a single transaction.

---

## 6. Security Implications

All four decisions maintain the existing security posture:

- **Separation of Duties** is preserved (requester ≠ approver enforced
  at both application and DB level).
- **Optimistic locking** continues to prevent concurrent resolution
  conflicts.
- **Audit trail** is maintained for every state transition via
  `approval_history` (H7) and `audit_log` (H6).
- **Representative isolation** is unaffected — approval requests are
  scoped to the requester's identity chain.
- **No new attack surface** — the decisions define policies, not new
  endpoints or commands.

---

## 7. Implementation Implications

**Zero production code changes required.** All four decisions are
compatible with the existing codebase:

- `approval_service.py`: No changes. The service already supports
  nullable `assigned_approver_id`, `cancel_request()`, and list queries.
- `approval_execution_service.py`: No changes.
- `bot_command_service.py`: No changes.
- `approval_request` model: No schema changes.
- `approval_history` model: No schema changes.

**Tests added:** Focused tests for state machine hardening (Step 6) and
payload/executor security (Step 8) are added to verify the policies.

---

## 8. Explicitly Deferred Work

| Item | Dependency | Phase |
|------|-----------|-------|
| Approval queue UI/endpoint | Future portal/admin bot | Post-Phase 8 |
| Approval timeout background job | Scheduler/worker infrastructure | Post-Phase 8 |
| `APPROVAL_MANAGE` permission | Admin cancellation endpoint | Post-Phase 8 |
| Bulk operation implementation | Next mutation commands | Post-Phase 8 |
| Command-specific approval permissions (ADJUST_APPROVE, RETURN_APPROVE) | Each command's implementation | Per command |

---

*Generated with Codebuff 🤖*
