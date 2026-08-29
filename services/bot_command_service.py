"""Platform-agnostic bot command service.

This module is the **single entry point** for processing bot commands.
It receives a normalized :class:`BotMessage`, resolves the sender's
identity via the session service, enforces RBAC, and dispatches to the
appropriate handler.

**Zero Telegram knowledge.**  This module imports nothing from
``telegram_adapter/`` -- the adapter is the only layer that knows about
Telegram's API.

Design:
    1. ``BotMessage`` / ``BotResponse`` -- platform-agnostic data models
       (defined below).
    2. ``process_message()`` -- the single entry point.  Resolves session,
       checks permissions, dispatches to a handler, returns a response.
    3. ``COMMAND_REGISTRY`` -- a dict mapping command names to handler
       functions.  Populated at module load time; Phase B adds more.
    4. Each handler receives the session + parsed args, returns a string.

Authorization:
    ``process_message()`` checks ``BOT_QUERY`` for read commands and
    ``BOT_WRITE`` for write commands (future).  The check is performed
    once per message, against the ``AppUser`` linked to the session's
    ``Representative``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.representative import Representative
from services import rbac_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalized message models (platform-agnostic)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BotMessage:
    """A platform-agnostic incoming bot message.

    The Telegram adapter normalizes Telegram's ``Update`` object into
    this shape.  A future Bale adapter would do the same for Bale's
    message format.
    """

    #: Platform-specific user identifier (e.g. Telegram numeric user id).
    platform_user_id: str

    #: Platform code (e.g. "TELEGRAM").
    platform_code: str

    #: Raw text of the message (e.g. "/orders" or "/order abc-123").
    text: str

    #: Optional: parsed command name (e.g. "orders").  If ``None``, the
    #: adapter or processor will extract it from ``text``.
    command: str | None = None

    #: Optional: parsed arguments string (e.g. "abc-123" for "/order abc-123").
    args: str = ""

    #: Platform-specific message metadata (e.g. Telegram message id).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BotResponse:
    """A platform-agnostic outgoing bot response.

    The Telegram adapter formats this into Telegram's ``sendMessage``
    API call.  A future Bale adapter would format it differently.
    """

    #: Response text (plain text or Markdown).
    text: str

    #: Optional: parse mode hint for the adapter (e.g. "Markdown", "HTML").
    parse_mode: str | None = None

    #: Optional: reply-to message id (for threading).
    reply_to_message_id: str | None = None


# ---------------------------------------------------------------------------
# Permission constants
# ---------------------------------------------------------------------------

#: Read-only bot commands.
BOT_QUERY_PERMISSION = "BOT_QUERY"

#: Write bot commands (future -- Phase B).
BOT_WRITE_PERMISSION = "BOT_WRITE"

#: Approval authority -- required for /pending, /approve, /reject.
APPROVE_PERMISSION = "APPROVE"


# ---------------------------------------------------------------------------
# Command handler type
# ---------------------------------------------------------------------------

#: A command handler: receives (session, app_user, representative, args_str)
#: and returns a response string.
CommandHandler = Callable[
    [Session, AppUser, Representative, str],
    str,
]


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

#: Maps lowercase command name -> handler function.
COMMAND_REGISTRY: dict[str, CommandHandler] = {}


def _register_command(
    name: str,
    required_permission: str = BOT_QUERY_PERMISSION,
    approval_required: bool = False,
) -> Callable[[CommandHandler], CommandHandler]:
    """Decorator to register a command handler with its required permission.

    ``approval_required`` (default ``False``): when ``True``, the command
    creates an ``approval_request`` before executing the mutation.  The
    handler is deferred until the request is approved.

    Usage::

        @_register_command("balance")
        def handle_balance(session, user, rep, args):
            ...

        @_register_command(
            "create-order",
            required_permission=BOT_WRITE_PERMISSION,
            approval_required=True,
        )
        def handle_create_order(session, user, rep, args):
            ...
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        COMMAND_REGISTRY[name] = func
        func._required_permission = required_permission  # type: ignore[attr-defined]
        func._approval_required = approval_required  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# Core entry point
# ---------------------------------------------------------------------------

class UnboundSessionError(LookupError):
    """Raised when an incoming message has no linked session."""

    def __init__(self, platform_code: str, platform_user_id: str) -> None:
        super().__init__(
            f"No linked bot session for {platform_code} user {platform_user_id}. "
            f"Please bind your account first with /link."
        )
        self.platform_code = platform_code
        self.platform_user_id = platform_user_id


class PermissionDeniedError(PermissionError):
    """Raised when the sender lacks the required permission."""

    def __init__(self, permission_code: str) -> None:
        super().__init__(f"Missing required permission '{permission_code}'.")
        self.permission_code = permission_code


class ApprovalRequiredError(PermissionError):
    """Raised when a command requires approval but no approval exists.

    The ``approval_request_id`` is set after the request is created, so
    the caller can reference it in the response.
    """

    def __init__(self, command: str, approval_request_id: uuid.UUID | None = None) -> None:
        super().__init__(
            f"Command '/{command}' requires approval. "
            f"Approval request pending."
        )
        self.command = command
        self.approval_request_id = approval_request_id


def _find_user_by_representative(db: Session, rep_id: uuid.UUID) -> AppUser | None:
    """Find the AppUser linked to a Representative via representative_id (FK).

    This is the correct lookup: AppUser.representative_id references
    Representative.id.  AppUser.id is a different PK and must NOT be
    used as the lookup key.
    """
    return db.execute(
        select(AppUser).where(AppUser.representative_id == rep_id)
    ).scalar_one_or_none()


def _parse_command(text: str) -> tuple[str, str]:
    """Parse ``/command args...`` into (command, args_string)."""
    text = text.strip()
    if not text.startswith("/"):
        return ("", text)
    parts = text.split(maxsplit=1)
    command = parts[0].lstrip("/").lower()
    args = parts[1] if len(parts) > 1 else ""
    return command, args


def process_message(
    db: Session,
    *,
    message: BotMessage,
    get_session_fn: Any = None,
    get_user_fn: Any = None,
    get_rep_fn: Any = None,
    log_inbound_fn: Any = None,
    log_outbound_fn: Any = None,
) -> BotResponse:
    """Process an incoming bot message and return a response.

    This is the single entry point for all bot command processing.
    The ``get_session_fn`` / ``get_user_fn`` / ``get_rep_fn`` parameters
    allow dependency injection for testing.

    Flow:
        1. Parse the command from the message text.
        2. Look up the bot session via ``get_session_fn``.
        3. Load the AppUser and Representative for the session.
        4. Check RBAC permission for the command.
        5. Call the command handler.
        6. Log inbound/outbound messages.
        7. Return the response.

    Raises:
        UnboundSessionError: no linked session for this platform identity.
        PermissionDeniedError: sender lacks the command's required permission.
    """
    # Lazy imports to avoid circular dependencies at module load time.
    from services import bot_session_service
    from services.customer_service import CustomerNotFoundError

    if get_session_fn is None:
        get_session_fn = lambda db, pc, puid: bot_session_service.resolve_session(db, platform_code=pc, platform_user_id=puid)  # noqa: E501
    if get_user_fn is None:
        # Query by representative_id (FK), NOT by primary key --
        # AppUser.id and Representative.id are different PKs.
        get_user_fn = _find_user_by_representative
    if get_rep_fn is None:
        get_rep_fn = lambda db, rid: db.get(Representative, rid)
    if log_inbound_fn is None:
        log_inbound_fn = lambda **kw: bot_session_service.log_inbound(db, **kw)
    if log_outbound_fn is None:
        log_outbound_fn = lambda **kw: bot_session_service.log_outbound(db, **kw)

    # 1. Parse command.
    command, args = _parse_command(message.text)
    if not command:
        command = message.command or ""
        args = message.args or ""

    # 2. Resolve session.
    bot_session = get_session_fn(db, message.platform_code, message.platform_user_id)
    if bot_session is None:
        raise UnboundSessionError(message.platform_code, message.platform_user_id)

    # 3. Load user and representative.
    # AppUser is looked up by representative_id (FK), not by primary key.
    # A representative may have zero or one linked AppUser.
    app_user = get_user_fn(db, bot_session.representative_id)

    representative = get_rep_fn(db, bot_session.representative_id)
    if representative is None:
        representative = db.get(Representative, bot_session.representative_id)

    # 4. Log inbound.
    log_inbound_fn(
        bot_session_id=bot_session.id,
        platform_code=message.platform_code,
        raw_payload={"text": message.text, **message.metadata},
        command_parsed=command or None,
    )

    # 5. Check permission.
    if command in COMMAND_REGISTRY:
        handler = COMMAND_REGISTRY[command]
        required_perm = getattr(handler, "_required_permission", BOT_QUERY_PERMISSION)
        if app_user is None:
            # A bound session must have a linked AppUser.  If it does not,
            # no permission can be verified, so deny unconditionally.
            raise PermissionDeniedError(required_perm)
        if not rbac_service.user_has_permission(db, app_user.id, required_perm):
            raise PermissionDeniedError(required_perm)
    elif command in ("start", "help", "link", "unlink"):
        # These commands are always allowed (no permission check needed).
        pass
    else:
        # Unknown command.
        response_text = f"Unknown command: /{command}. Type /help for available commands."
        log_outbound_fn(
            bot_session_id=bot_session.id,
            platform_code=message.platform_code,
            raw_payload={"text": response_text},
            command_parsed=command or None,
        )
        return BotResponse(text=response_text)

    # 6. Dispatch to handler.
    try:
        if command in ("start", "help", "link", "unlink"):
            # Built-in commands handled here.
            response_text = _handle_builtin(command, args, bot_session, representative)
        elif command in COMMAND_REGISTRY:
            handler = COMMAND_REGISTRY[command]
            needs_approval = getattr(handler, "_approval_required", False)

            if needs_approval and app_user is not None:
                # --- Approval-gated write command ---
                # For approval-gated commands, the handler is NOT called
                # directly.  Instead, an approval request is created and
                # the handler is deferred until approval.
                #
                # If the handler's return type annotation includes 'dict'
                # in its type hint (via __annotations__), the handler
                # performs validation and returns a payload dict on
                # success or an error string on failure.  We call it
                # first for validation, then create the approval request
                # only if validation succeeds.
                #
                # Otherwise (legacy handlers), the approval request is
                # created without calling the handler.
                handler_annotations = getattr(handler, "__annotations__", {})
                return_type = handler_annotations.get("return", str)
                returns_dict = (
                    return_type is not None
                    and "dict" in str(return_type)
                )

                if returns_dict:
                    # New-style handler: call for validation first.
                    result = handler(db, app_user, representative, args)
                    if isinstance(result, dict):
                        # Validation passed — create approval request.
                        response_text = _handle_approval_required_command(
                            db,
                            command=command,
                            handler=handler,
                            app_user=app_user,
                            representative=representative,
                            args=args,
                            bot_session_id=bot_session.id,
                            payload=result,
                        )
                    else:
                        # Validation error — show to user.
                        response_text = result
                else:
                    # Legacy handler: create approval request directly.
                    response_text = _handle_approval_required_command(
                        db,
                        command=command,
                        handler=handler,
                        app_user=app_user,
                        representative=representative,
                        args=args,
                        bot_session_id=bot_session.id,
                    )
            else:
                # --- Direct execution (read-only or write without approval) ---
                response_text = handler(db, app_user, representative, args)
        else:
            response_text = f"Unknown command: /{command}. Type /help for available commands."
    except Exception as exc:
        logger.exception("Bot command '%s' failed", command)
        response_text = f"Error: {exc}"

    # 7. Log outbound.
    log_outbound_fn(
        bot_session_id=bot_session.id,
        platform_code=message.platform_code,
        raw_payload={"text": response_text},
        command_parsed=command or None,
    )

    return BotResponse(text=response_text)


def _handle_builtin(
    command: str,
    args: str,
    bot_session: Any,
    representative: Any,
) -> str:
    """Handle built-in commands (start, help, link, unlink)."""
    if command == "start":
        return (
            f"Welcome to the ERP Bot, {representative.person_name}!\n"
            "Type /help to see available commands."
        )
    elif command == "help":
        lines = [
            "Available commands:",
            "/me — Show your profile",
            "/balance — Show customer balances",
            "/orders — List your recent orders",
            "/order <id> — Show order details",
            "/inventory — Check stock levels",
            "/customers — List your customers",
            "/transfers — List your transfers",
            "/transfer <transfer_number> — Show transfer details",
            "/transfer-history <transfer_number> — Show transfer history",
            "",
            "Write commands (require BOT_WRITE):",
            "/create-order <customer> <product> <qty> [mode] — Create a new order",
            "/adjust <product> <type> <qty> <reason> [text] — Request a stock adjustment",
            "/return <order> <product> <qty> <reason> [text] — Request a product return",
            "/dispatch <transfer_number> — Dispatch a stock transfer",
            "/confirm <transfer_number> — Confirm receipt of a stock transfer",
            "/cancel-transfer <transfer_number> — Cancel a draft transfer",
            "/submit <order_number> — Submit a draft order for approval",
            "/cancel-order <order_number> — Cancel an order",
            "/backorder-resubmit <order_number> — Resubmit a backordered order",
            "/start-fulfillment <order_number> — Start fulfillment for a reserved order",
            "/ship <order_number> <sku> <qty> — Record a shipment",
            "/order-history <order_number> — View order status history",
            "",
            "Type /help at any time to see this list.",
        ]
        return "\n".join(lines)
    elif command == "link":
        return (
            "To link your Telegram account, please ask an administrator "
            "to generate a binding token, then send /link <token>."
        )
    elif command == "unlink":
        return "To unlink your account, please contact an administrator."
    return ""


# ---------------------------------------------------------------------------
# Approval-gated write command handler
# ---------------------------------------------------------------------------


def _handle_approval_required_command(
    db: Session,
    *,
    command: str,
    handler: CommandHandler,
    app_user: AppUser,
    representative: Any,
    args: str,
    bot_session_id: uuid.UUID,
    payload: dict | None = None,
) -> str:
    """Route a write command through the approval workflow.

    1. Check if an existing PENDING approval request already exists for
       this command + representative.
    2. If yes, return "already pending" message.
    3. If no, create a new PENDING approval request and return
       "pending approval" message.

    The actual mutation (calling ``handler(...)``) is deferred until an
    approver explicitly approves the request via ``approval_service``.

    This function creates the approval request using
    ``entity_type=f'bot_command:{command}'`` and ``entity_id`` derived
    from the bot session (to scope the request to this representative).

    Per ADR-008, the invariant is preserved:
    ``approval_required=True`` commands never execute without a matching
    ``approval.granted`` event.
    """
    from services.approval_service import (
        ApprovalRequestAlreadyExistsError,
        create_approval_request,
        get_pending_request,
    )

    # Use the bot_session_id as the entity_id for the approval request.
    # This scopes the request to this specific bot interaction.
    entity_type = f"bot_command:{command}"
    entity_id = bot_session_id

    # Check for existing PENDING request.
    existing = get_pending_request(db, entity_type, entity_id)
    if existing is not None:
        return (
            f"Your /{command} request is already pending approval "
            f"(request {existing.id}). "
            f"Please wait for an administrator to review it."
        )

    # Build reason text from payload if available.
    reason = f"Bot command /{command} from representative {representative.person_name}"
    if payload is not None:
        parts = []
        if "customer_code" in payload:
            parts.append(f"Customer: {payload['customer_code']}")
        if "product_sku" in payload:
            parts.append(f"Product: {payload['product_sku']}")
        if "qty" in payload:
            parts.append(f"Qty: {payload['qty']}")
        if "warehouse_code" in payload:
            parts.append(f"Warehouse: {payload['warehouse_code']}")
        if "fulfillment_mode" in payload:
            parts.append(f"Mode: {payload['fulfillment_mode']}")
        reason = ", ".join(parts)

    # Create a new PENDING approval request.
    try:
        request = create_approval_request(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            requested_by=app_user.id,
            reason_text=reason,
            payload=payload,
        )
        # Build a detailed response from payload.
        if payload is not None:
            details = []
            if "customer_code" in payload:
                details.append(f"  Customer: {payload['customer_code']}")
            if "product_sku" in payload:
                details.append(f"  Product: {payload['product_sku']}")
            if "qty" in payload:
                details.append(f"  Quantity: {payload['qty']}")
            if "warehouse_code" in payload:
                details.append(f"  Warehouse: {payload['warehouse_code']}")
            if "fulfillment_mode" in payload:
                details.append(f"  Mode: {payload['fulfillment_mode']}")
            detail_str = "\n".join(details)
            return (
                f"Your /{command} request has been submitted for approval.\n"
                f"{detail_str}\n"
                f"An administrator will review it shortly."
            )
        return (
            f"Your /{command} request has been submitted for approval. "
            f"An administrator will review it shortly."
        )
    except ApprovalRequestAlreadyExistsError:
        # Race condition: another request was created between our check
        # and insert. Return the pending message.
        return (
            f"Your /{command} request has been submitted for approval. "
            f"An administrator will review it shortly."
        )


# ---------------------------------------------------------------------------
# Read-only command handlers (v1)
# ---------------------------------------------------------------------------

@_register_command("me")
def handle_me(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show the user's profile information."""
    lines = [
        f"Name: {rep.person_name}",
        f"Code: {rep.code}",
        f"Status: {rep.status}",
    ]
    if user is not None:
        lines.append(f"Username: {user.username}")
        lines.append(f"Email: {user.email}")
    return "\n".join(lines)


@_register_command("balance")
def handle_balance(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show customer balances for the representative's customers.

    Uses ``representative_scope_service.resolve_representative_customers()``
    to resolve the representative's assigned customers (ADR-007 §1),
    then ``customer_ledger_service.get_balance()`` for each customer.

    Per ADR-007 §5, all scope logic lives in the service layer.
    The command handler delegates entirely.

    Returns a concise, Telegram-safe balance list.
    """
    from services import customer_ledger_service, representative_scope_service

    customers = representative_scope_service.resolve_representative_customers(
        session, rep.id,
    )

    if not customers:
        return "No customers assigned to you."

    lines = ["Customer balances:"]
    for c in customers:
        balance = customer_ledger_service.get_balance(session, c.id)
        lines.append(f"  {c.code} | {c.name} | {balance}")
    return "\n".join(lines)


@_register_command("orders")
def handle_orders(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """List recent orders for the representative.

    Uses ``order_service.list_orders()`` with ``representative_id``
    scoped to the session's representative.  Per ADR-007, data is
    resolved through the scope service layer, never globally.

    Returns a concise, Telegram-safe list of order summaries.
    """
    from services import order_service

    limit = 10
    orders = list(order_service.list_orders(
        session,
        representative_id=rep.id,
        limit=limit,
    ))

    if not orders:
        return "No orders found."

    lines = ["Your recent orders:"]
    for o in orders:
        # Format: order_number | state | grand_total
        lines.append(
            f"  {o.order_number} | {o.state} | {o.grand_total}"
        )
    if len(orders) == limit:
        lines.append(f"\n(Showing last {limit} orders)")
    return "\n".join(lines)


@_register_command("order")
def handle_order(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show order details for a specific order ID.

    Uses ``order_service.get_order_for_representative()`` which
    enforces cross-representative access prohibition (ADR-007 §3).
    Never calls unrestricted ``get_order()``.

    Handles:
        - Missing argument → usage hint
        - Malformed UUID → clear error message
        - Nonexistent order → "not found"
        - Access denied (wrong rep) → "access denied"
    """
    import uuid as _uuid

    from services import order_service

    order_id_str = args.strip()
    if not order_id_str:
        return "Usage: /order <order_id>"

    try:
        order_id = _uuid.UUID(order_id_str)
    except (ValueError, AttributeError):
        return f"Invalid order ID: '{order_id_str}'. Use a valid UUID."

    try:
        order = order_service.get_order_for_representative(
            session, order_id, rep.id,
        )
    except order_service.OrderNotFoundError:
        return f"Order '{order_id_str}' not found."
    except order_service.OrderAccessDeniedError:
        return "Access denied: this order does not belong to you."

    # Safe, concise order details — no internal IDs leaked.
    lines = [
        f"Order: {order.order_number}",
        f"State: {order.state}",
        f"Type: {order.order_type}",
        f"Total: {order.grand_total}",
        f"Ordered: {order.ordered_at.strftime('%Y-%m-%d %H:%M') if order.ordered_at else 'N/A'}",
    ]
    return "\n".join(lines)


@_register_command("inventory")
def handle_inventory(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Check stock levels for the representative's assigned warehouse(s).

    Uses ``representative_scope_service.resolve_representative_warehouses()``
    to resolve the representative's assigned warehouses (ADR-007 §2),
    then ``inventory_service.list_warehouse_balances()`` for each.

    Per ADR-007 §5, all scope logic lives in the service layer.

    Returns a concise, Telegram-safe inventory list.
    """
    from services import inventory_service, representative_scope_service

    warehouses = representative_scope_service.resolve_representative_warehouses(
        session, rep.id, primary_only=True,
    )

    if not warehouses:
        return "No warehouse assigned to you."

    wh = warehouses[0]
    balances = inventory_service.list_warehouse_balances(
        session, warehouse_id=wh.id, limit=10,
    )

    if not balances:
        return f"No stock in {wh.code}."

    lines = [f"Stock in {wh.code}:"]
    for b in balances:
        lines.append(f"  {b['sku']} | {b['name']} | {b['balance']}")
    if len(balances) == 10:
        lines.append(f"\n(Showing top 10 products by quantity)")
    return "\n".join(lines)


@_register_command("customers")
def handle_customers(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """List customers assigned to the representative.

    Uses ``representative_scope_service.resolve_representative_customers()``
    which respects assignment time-window semantics (ADR-007 §1).
    Never calls ``customer_service.list_customers()`` globally.

    Returns a concise, Telegram-safe customer list.
    """
    from services import representative_scope_service

    customers = representative_scope_service.resolve_representative_customers(
        session, rep.id,
    )

    if not customers:
        return "No customers assigned to you."

    lines = ["Your customers:"]
    for c in customers:
        lines.append(f"  {c.code} | {c.name} | {c.status}")
    return "\n".join(lines)


@_register_command("transfers")
def handle_transfers(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """List transfers visible to the representative.

    Shows both outbound (source warehouse = rep's) and inbound
    (destination warehouse = rep's) transfers.
    Uses ``transfers_cmd.list_visible_transfers()`` which enforces
    warehouse scope at the service layer.
    """
    limit = 10
    transfers = list_visible_transfers(session, representative_id=rep.id, limit=limit)

    if not transfers:
        return "No transfers found."

    lines = ["Transfers:"]
    for t in transfers:
        direction_tag = "OUT" if t["direction"] == "OUTBOUND" else "IN"
        lines.append(
            f"  {t['transfer_number']} | {direction_tag} | "
            f"{t['source_code']} -> {t['dest_code']} | {t['state']}"
        )
    if len(transfers) == limit:
        lines.append(f"\n(Showing last {limit} transfers)")
    return "\n".join(lines)


@_register_command("transfer")
def handle_transfer(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show details for a specific transfer by transfer number.

    Uses ``transfers_cmd.get_visible_transfer()`` which enforces
    warehouse scope at the service layer in a single authorization-aware
    query (prevents IDOR).
    """
    transfer_number = args.strip()
    if not transfer_number:
        return "Usage: /transfer <transfer_number>"

    detail = get_visible_transfer(
        session, representative_id=rep.id, transfer_number=transfer_number,
    )

    if detail is None:
        return f"Transfer '{transfer_number}' not found."

    direction_tag = "OUTBOUND" if detail["direction"] == "OUTBOUND" else "INBOUND"
    lines = [
        f"Transfer: {detail['transfer_number']}",
        f"Direction: {direction_tag}",
        f"Status: {detail['state']}",
        f"Source: {detail['source_code']}",
        f"Destination: {detail['dest_code']}",
    ]

    if detail["requested_at"]:
        lines.append(f"Created: {detail['requested_at'].strftime('%Y-%m-%d %H:%M')}")
    if detail["dispatched_at"]:
        lines.append(f"Dispatched: {detail['dispatched_at'].strftime('%Y-%m-%d %H:%M')}")
    if detail["received_at"]:
        lines.append(f"Received: {detail['received_at'].strftime('%Y-%m-%d %H:%M')}")

    if detail["lines"]:
        lines.append("")
        lines.append("Items:")
        for i, tl in enumerate(detail["lines"], 1):
            lines.append(f"  {i}. {tl['product']} | {tl['qty_requested']} | dispatched: {tl['qty_dispatched']} | received: {tl['qty_received']}")

    return "\n".join(lines)


@_register_command("transfer-history")
def handle_transfer_history(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show the state-change history for a specific transfer.

    Uses ``transfers_cmd.get_visible_transfer_history()`` which enforces
    warehouse scope at the service layer in a single authorization-aware
    query.
    """
    transfer_number = args.strip()
    if not transfer_number:
        return "Usage: /transfer-history <transfer_number>"

    result = get_visible_transfer_history(
        session, representative_id=rep.id, transfer_number=transfer_number,
    )

    if result is None:
        return f"Transfer '{transfer_number}' not found."

    direction_tag = "OUTBOUND" if result["direction"] == "OUTBOUND" else "INBOUND"
    lines = [
        f"Transfer History: {result['transfer_number']}",
        f"Direction: {direction_tag}",
        f"Current Status: {result['state']}",
        "",
    ]

    history = result["history"]
    if not history:
        lines.append("No history records found.")
    else:
        for i, h in enumerate(history, 1):
            lines.append(f"{i}. {h['from_state']} -> {h['to_state']}")
            actor_str = h['actor']
            date_str = h['event_at'].strftime('%Y-%m-%d %H:%M') if h['event_at'] else '???'
            lines.append(f"   Actor: {actor_str}")
            lines.append(f"   Date: {date_str}")
            if h['note']:
                lines.append(f"   Note: {h['note']}")
            lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Write command handlers (v2) — Tier 3 with approval
# ---------------------------------------------------------------------------

from services.bot_commands.create_order import (
    validate_and_build_payload as _validate_create_order,
    execute_create_order as _execute_create_order,
)
from services.bot_commands.adjust import (
    validate_and_build_payload as _validate_adjust,
    execute_adjust as _execute_adjust,
)
from services.bot_commands.return_cmd import (
    validate_and_build_payload as _validate_return,
    execute_return as _execute_return,
)
from services.bot_commands.confirm_cmd import (
    validate_and_build_payload as _validate_confirm,
    execute_confirm as _execute_confirm,
)
from services.bot_commands.dispatch_cmd import (
    validate_and_build_payload as _validate_dispatch,
    execute_dispatch as _execute_dispatch,
)
from services.bot_commands.transfers_cmd import list_visible_transfers, get_visible_transfer, get_visible_transfer_history
from services.bot_commands.cancel_transfer_cmd import (
    validate_and_build_payload as _validate_cancel_transfer,
    execute_cancel_transfer as _execute_cancel_transfer,
)
from services.bot_commands.submit_cmd import (
    validate_and_build_payload as _validate_submit,
    execute_submit as _execute_submit,
)
from services.bot_commands.cancel_order_cmd import (
    validate_and_build_payload as _validate_cancel_order,
    execute_cancel_order as _execute_cancel_order,
)
from services.bot_commands.order_history_cmd import get_order_history_display
from services.bot_commands.backorder_resubmit_cmd import (
    validate_and_build_payload as _validate_backorder_resubmit,
    execute_backorder_resubmit as _execute_backorder_resubmit,
)
from services.bot_commands.start_fulfillment_cmd import (
    validate_and_build_payload as _validate_start_fulfillment,
    execute_start_fulfillment as _execute_start_fulfillment,
)
from services.bot_commands.ship_cmd import (
    validate_and_build_payload as _validate_ship,
    execute_ship as _execute_ship,
)


@_register_command(
    "create-order",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=True,
)
def handle_create_order(session: Session, user: AppUser, rep: Representative, args: str) -> dict | str:
    """Validate /create-order arguments and build payload.

    Syntax: /create-order <customer_code> <product_sku> <qty> [fulfillment_mode]

    Returns a dict payload on success (caller creates approval request),
    or an error string on failure.

    Per ADR-008 §6, the mutation is NOT executed before approval.
    """
    # Validate and build payload via the dedicated module.
    payload = _validate_create_order(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        # Validation returned an error message.
        return payload

    # Return payload dict — caller will create approval request.
    return payload


@_register_command(
    "adjust",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=True,
)
def handle_adjust(session: Session, user: AppUser, rep: Representative, args: str) -> dict | str:
    """Validate /adjust arguments and build payload.

    Syntax: /adjust <product_sku> <adjustment_type> <delta_quantity>
                     <reason_code> [reason_text]

    Returns a dict payload on success (caller creates approval request),
    or an error string on failure.

    Per ADR-008 §6, the mutation is NOT executed before approval.
    """
    payload = _validate_adjust(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return payload


@_register_command(
    "return",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=True,
)
def handle_return(session: Session, user: AppUser, rep: Representative, args: str) -> dict | str:
    """Validate /return arguments and build payload.

    Syntax: /return <order_number> <product_sku> <quantity>
                     <reason_code> [reason_text]

    Returns a dict payload on success (caller creates approval request),
    or an error string on failure.

    Per ADR-008 §6, the mutation is NOT executed before approval.
    """
    payload = _validate_return(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return payload


@_register_command(
    "confirm",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_confirm(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /confirm (stock transfer receipt confirmation).

    Syntax: /confirm <transfer_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical stock_transfer_service.receive_transfer().
    """
    payload = _validate_confirm(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    # Tier 2: execute directly, no approval.
    return _execute_confirm(session, payload, actor_user_id=user.id)


@_register_command(
    "dispatch",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_dispatch(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /dispatch (stock transfer dispatch).

    Syntax: /dispatch <transfer_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical stock_transfer_service.dispatch_transfer().
    """
    payload = _validate_dispatch(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    # Tier 2: execute directly, no approval.
    return _execute_dispatch(session, payload, actor_user_id=user.id)


@_register_command(
    "cancel-transfer",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_cancel_transfer(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /cancel-transfer.

    Syntax: /cancel-transfer <transfer_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Source warehouse scope enforced. Only DRAFT transfers can be cancelled.
    """
    payload = _validate_cancel_transfer(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return _execute_cancel_transfer(session, payload, actor_user_id=user.id)


@_register_command(
    "submit",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_submit(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /submit (order DRAFT → PENDING_APPROVAL).

    Syntax: /submit <order_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical order_service.submit_order().
    """
    payload = _validate_submit(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    # Tier 2: execute directly, no approval.
    return _execute_submit(session, payload, actor_user_id=user.id)


@_register_command(
    "cancel-order",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_cancel_order(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /cancel-order.

    Syntax: /cancel-order <order_number> [reason]

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical order_service.cancel_order().
    """
    payload = _validate_cancel_order(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return _execute_cancel_order(session, payload, actor_user_id=user.id)


@_register_command(
    "backorder-resubmit",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_backorder_resubmit(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /backorder-resubmit.

    Syntax: /backorder-resubmit <order_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical order_service.resubmit_order().
    """
    payload = _validate_backorder_resubmit(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return _execute_backorder_resubmit(session, payload, actor_user_id=user.id)


@_register_command(
    "start-fulfillment",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_start_fulfillment(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /start-fulfillment.

    Syntax: /start-fulfillment <order_number>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical order_service.start_fulfillment().
    """
    payload = _validate_start_fulfillment(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return _execute_start_fulfillment(session, payload, actor_user_id=user.id)


@_register_command(
    "ship",
    required_permission=BOT_WRITE_PERMISSION,
    approval_required=False,
)
def handle_ship(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Validate and execute /ship.

    Syntax: /ship <order_number> <product_sku> <quantity>

    Tier 2 command: BOT_WRITE required, no approval.
    Executes directly via canonical order_service.ship_order().
    """
    payload = _validate_ship(session, rep=rep, user=user, args=args)
    if isinstance(payload, str):
        return payload
    return _execute_ship(session, payload, actor_user_id=user.id)


@_register_command("order-history", required_permission=BOT_QUERY_PERMISSION)
def handle_order_history(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show the state-transition history for a specific order.

    Syntax: /order-history <order_number>

    Tier 1 command: BOT_QUERY required, read-only.
    Uses the existing order_status_history via order_history_cmd.
    """
    return get_order_history_display(session, rep=rep, user=user, args=args)


# Register the executors for deferred execution after approval.
from services.approval_execution_service import EXECUTOR_REGISTRY  # noqa: E402
EXECUTOR_REGISTRY["create-order"] = _execute_create_order
EXECUTOR_REGISTRY["adjust"] = _execute_adjust
EXECUTOR_REGISTRY["return"] = _execute_return


# ---------------------------------------------------------------------------
# Approval queue commands — require APPROVE permission
# ---------------------------------------------------------------------------


@_register_command("pending", required_permission=APPROVE_PERMISSION)
def handle_pending(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Show pending approval requests.

    Lists all PENDING approval requests visible to the current user.
    Uses business-facing approval numbers (APR-XXXXXXXX), never UUIDs.
    """
    from services.approval_service import list_pending_requests
    from database.models.app_user import AppUser as _AppUser

    pending = list_pending_requests(session)

    if not pending:
        return "No pending approval requests."

    lines = ["Pending approval requests:"]
    for req in pending:
        # Resolve requester name.
        requester = session.get(_AppUser, req.requested_by)
        requester_name = "Unknown"
        if requester is not None:
            from database.models.representative import Representative as _Rep
            if requester.representative_id is not None:
                rep_row = session.get(_Rep, requester.representative_id)
                if rep_row is not None:
                    requester_name = rep_row.person_name
            if requester_name == "Unknown":
                requester_name = requester.username

        # Map entity_type to human-readable label.
        entity_label = {
            "bot_command:create-order": "Create Order",
            "bot_command:adjust": "Stock Adjustment",
            "bot_command:return": "Product Return",
        }.get(req.entity_type, req.entity_type)

        date_str = req.requested_at.strftime("%Y-%m-%d %H:%M") if req.requested_at else "N/A"
        lines.append("")
        lines.append(f"{req.approval_number}")
        lines.append(f"  Type: {entity_label}")
        lines.append(f"  Requester: {requester_name}")
        lines.append(f"  Date: {date_str}")
        if req.reason_text:
            lines.append(f"  Reason: {req.reason_text}")

    return "\n".join(lines)


@_register_command("approve", required_permission=APPROVE_PERMISSION)
def handle_approve(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Approve a pending request and execute the deferred mutation.

    Syntax: /approve <APR-XXXXXXXX>
    """
    from services.approval_service import (
        SeparationOfDutiesError,
        approve_request,
        get_approval_request_by_number,
    )
    from services.approval_execution_service import (
        execute_approved_request,
        ApprovalNotApprovedError,
    )

    ref = args.strip().upper()
    if not ref:
        return "Usage: /approve <APR-XXXXXXXX>"

    # Validate format.
    if not ref.startswith("APR-") or len(ref) != 12:
        return f"Invalid approval reference: '{ref}'. Format: APR-XXXXXXXX"

    # Look up by approval_number.
    try:
        request = get_approval_request_by_number(session, ref)
    except Exception:
        return f"Approval request '{ref}' not found."

    # Check status.
    if request.status != "PENDING":
        return f"Request '{ref}' is {request.status}, not PENDING."

    # Authorization: requester cannot approve own request.
    if request.requested_by == user.id:
        return "You cannot approve your own request (separation of duties)."

    # Approve.
    try:
        approve_request(
            session, request_id=request.id, approver_id=user.id,
        )
    except SeparationOfDutiesError:
        return "You cannot approve your own request (separation of duties)."
    except Exception as exc:
        return f"Approval failed: {exc}"

    # Execute the deferred mutation.
    try:
        result = execute_approved_request(
            session, request_id=request.id, approver_id=user.id,
        )
    except Exception as exc:
        return (
            f"Request '{ref}' was approved but execution failed: {exc}\n"
            f"The approval has been recorded. Manual intervention may be needed."
        )

    return (
        f"Request '{ref}' approved and executed successfully.\n"
        f"{result}"
    )


@_register_command("reject", required_permission=APPROVE_PERMISSION)
def handle_reject(session: Session, user: AppUser, rep: Representative, args: str) -> str:
    """Reject a pending request.

    Syntax: /reject <APR-XXXXXXXX> [reason]
    """
    from services.approval_service import (
        SeparationOfDutiesError,
        get_approval_request_by_number,
        reject_request,
    )

    parts = args.strip().split(maxsplit=1)
    ref = parts[0].upper() if parts else ""
    reason = parts[1] if len(parts) > 1 else None

    if not ref:
        return "Usage: /reject <APR-XXXXXXXX> [reason]"

    # Validate format.
    if not ref.startswith("APR-") or len(ref) != 12:
        return f"Invalid approval reference: '{ref}'. Format: APR-XXXXXXXX"

    # Look up by approval_number.
    try:
        request = get_approval_request_by_number(session, ref)
    except Exception:
        return f"Approval request '{ref}' not found."

    # Check status.
    if request.status != "PENDING":
        return f"Request '{ref}' is {request.status}, not PENDING."

    # Authorization: requester cannot reject own request.
    if request.requested_by == user.id:
        return "You cannot reject your own request (separation of duties)."

    # Reject.
    try:
        reject_request(
            session, request_id=request.id, approver_id=user.id, note=reason,
        )
    except SeparationOfDutiesError:
        return "You cannot reject your own request (separation of duties)."
    except Exception as exc:
        return f"Rejection failed: {exc}"

    return f"Request '{ref}' rejected."


__all__ = [
    "APPROVE_PERMISSION",
    "BOT_QUERY_PERMISSION",
    "BOT_WRITE_PERMISSION",
    "BotMessage",
    "BotResponse",
    "COMMAND_REGISTRY",
    "PermissionDeniedError",
    "UnboundSessionError",
    "process_message",
]
