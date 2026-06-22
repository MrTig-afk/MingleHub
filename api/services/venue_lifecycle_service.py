"""Venue billing lifecycle: cancel, suspend, reactivate.

All status transitions live here. Every public function takes a conn (asyncpg
connection) so they compose inside a caller's transaction.

State machine:
  active     --> cancelled   (owner voluntary OR admin)
  active     --> suspended   (dunning: failed invoice > 7d unpaid OR admin)
  cancelled  --> active      (owner self-reactivate within 7d OR admin)
  suspended  --> active      (webhook paid if suspension_reason='dunning' OR admin)
  admin can force: active <-> suspended <-> cancelled (any direction)

Disallowed self-service paths are documented in cancel_venue / reactivate_venue.
"""
from api.services.billing_service import _period_window, recompute_invoices
from api.services.stripe_service import sync_invoice


async def cancel_venue(conn, venue_id: str, reason: str) -> dict:
    """Owner voluntary cancel. Idempotent.

    Raises ValueError('venue_suspended') if the venue is currently suspended
    (owner must settle their balance first; prevents balance-dodge via cancel
    then reactivate clean).
    """
    row = await conn.fetchrow(
        "SELECT status, cancelled_at FROM venues WHERE id = $1 FOR UPDATE",
        venue_id,
    )
    if not row:
        raise LookupError("venue_not_found")

    if row["status"] == "cancelled":
        # Idempotent: already cancelled — return existing state without re-issuing.
        return {
            "status": "cancelled",
            "cancelled_at": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
            "already_cancelled": True,
            "final_invoice_id": None,
        }

    if row["status"] == "suspended":
        raise ValueError("venue_suspended")

    await conn.execute(
        """
        UPDATE venues SET status = 'cancelled', cancelled_at = NOW(),
            cancellation_reason = $1, updated_at = NOW()
        WHERE id = $2
        """,
        reason, venue_id,
    )

    final_invoice_id = await _issue_final_invoice(conn, venue_id)
    await archive_payment_methods(conn, venue_id)

    new_row = await conn.fetchrow(
        "SELECT cancelled_at FROM venues WHERE id = $1", venue_id
    )
    return {
        "status": "cancelled",
        "cancelled_at": new_row["cancelled_at"].isoformat() if new_row["cancelled_at"] else None,
        "final_invoice_id": final_invoice_id,
    }


async def reactivate_venue(conn, venue_id: str) -> dict:
    """Owner self-reactivate within the 7-day window. Idempotent.

    Raises ValueError('venue_suspended') if suspended (must pay, not reactivate).
    Raises ValueError('reactivation_window_expired') if more than 7 days since cancel.
    """
    row = await conn.fetchrow(
        "SELECT status, cancelled_at FROM venues WHERE id = $1 FOR UPDATE",
        venue_id,
    )
    if not row:
        raise LookupError("venue_not_found")

    if row["status"] == "active":
        return {"status": "active", "already_active": True}

    if row["status"] == "suspended":
        raise ValueError("venue_suspended")

    if row["status"] != "cancelled":
        raise ValueError("invalid_status")

    # Check 7-day window.
    within_window = await conn.fetchval(
        "SELECT cancelled_at > NOW() - INTERVAL '7 days' FROM venues WHERE id = $1",
        venue_id,
    )
    if not within_window:
        raise ValueError("reactivation_window_expired")

    await conn.execute(
        """
        UPDATE venues SET status = 'active', cancelled_at = NULL,
            cancellation_reason = NULL, updated_at = NOW()
        WHERE id = $1
        """,
        venue_id,
    )
    return {"status": "active"}


async def admin_change_status(
    conn, venue_id: str, new_status: str, reason: str, actor_id: str, ip: str,
) -> dict:
    """Admin force any status transition. Writes audit log + config override row.

    No-ops if current status already equals new_status (returns updated=False).
    """
    # Import here to avoid circular imports (admin_router imports from here).
    from api.routers.admin_router import _write_audit_log
    import uuid

    row = await conn.fetchrow(
        """
        SELECT status, cancelled_at, suspended_at FROM venues WHERE id = $1 FOR UPDATE
        """,
        venue_id,
    )
    if not row:
        raise LookupError("venue_not_found")

    old_status = row["status"]
    if old_status == new_status:
        return {"updated": False}

    if new_status == "cancelled":
        await conn.execute(
            """
            UPDATE venues SET status = 'cancelled', cancelled_at = NOW(),
                cancellation_reason = $1, suspended_at = NULL, suspension_reason = NULL,
                updated_at = NOW()
            WHERE id = $2
            """,
            reason, venue_id,
        )
        await _issue_final_invoice(conn, venue_id)
        await archive_payment_methods(conn, venue_id)

    elif new_status == "suspended":
        await conn.execute(
            """
            UPDATE venues SET status = 'suspended', suspended_at = NOW(),
                suspension_reason = 'admin', cancelled_at = NULL, cancellation_reason = NULL,
                updated_at = NOW()
            WHERE id = $1
            """,
            venue_id,
        )

    elif new_status == "active":
        await conn.execute(
            """
            UPDATE venues SET status = 'active', cancelled_at = NULL, suspended_at = NULL,
                cancellation_reason = NULL, suspension_reason = NULL, updated_at = NOW()
            WHERE id = $1
            """,
            venue_id,
        )

    # Config override row (same pattern as the generic field loop in admin_router).
    await conn.execute(
        """
        INSERT INTO venue_config_overrides
            (id, venue_id, field_name, old_value, new_value, reason, changed_by, created_at)
        VALUES ($1, $2, 'status', $3, $4, $5, $6, NOW())
        """,
        str(uuid.uuid4()), venue_id, str(old_status), str(new_status), reason, actor_id,
    )

    await _write_audit_log(
        conn, actor_id, "venue_status_change", "venue", venue_id,
        {"old_status": old_status, "new_status": new_status, "reason": reason},
        ip,
    )

    return {"updated": True, "old_status": old_status, "new_status": new_status}


async def suspend_for_nonpayment(conn, venue_id: str) -> bool:
    """Dunning: suspend a venue whose invoice has been failed > 7 days.

    Returns True if the venue was active and is now suspended.
    Returns False if already not active (no-op).
    """
    row = await conn.fetchrow(
        """
        UPDATE venues SET status = 'suspended', suspended_at = NOW(),
            suspension_reason = 'dunning', updated_at = NOW()
        WHERE id = $1 AND status = 'active'
        RETURNING id
        """,
        venue_id,
    )
    return row is not None


async def auto_reactivate_on_payment(conn, venue_id: str) -> bool:
    """Auto-reactivate a venue suspended for non-payment ONLY.

    Called when a Stripe webhook marks an invoice paid. Does NOT reactivate
    admin-suspended or cancelled venues. Idempotent (no-op if already active).

    Returns True if reactivated, False otherwise.
    """
    row = await conn.fetchrow(
        """
        UPDATE venues SET status = 'active', suspended_at = NULL,
            suspension_reason = NULL, updated_at = NOW()
        WHERE id = $1 AND status = 'suspended' AND suspension_reason = 'dunning'
        RETURNING id
        """,
        venue_id,
    )
    return row is not None


async def check_dunning_suspensions(conn) -> int:
    """Sweep: find active non-test venues whose invoice has been failed > 7 days.

    Called at the end of the nightly rollup (scripts/rollup_billing.py).
    Returns count of venues newly suspended.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT i.venue_id
        FROM invoices i
        JOIN venues v ON v.id = i.venue_id
        WHERE i.status = 'failed'
          AND i.updated_at < NOW() - INTERVAL '7 days'
          AND v.status = 'active'
          AND v.is_test = FALSE
        """
    )
    count = 0
    for row in rows:
        suspended = await suspend_for_nonpayment(conn, str(row["venue_id"]))
        if suspended:
            count += 1
    return count


async def _issue_final_invoice(conn, venue_id: str):
    """Recompute current month invoice, mark it is_final=TRUE, sync to Stripe.

    Idempotent: if a final invoice already exists for this venue, return its id.
    Returns the invoice id string, or None if no billable activity this month.
    """
    # Idempotency: a final invoice already issued → return it unchanged.
    existing_final = await conn.fetchval(
        "SELECT id FROM invoices WHERE venue_id = $1 AND is_final = TRUE",
        venue_id,
    )
    if existing_final:
        return str(existing_final)

    # Recompute the current month across all venues (idempotent rollup).
    await recompute_invoices(conn)

    win = await _period_window(conn, None)
    period_start = win["period_start"]

    invoice_row = await conn.fetchrow(
        "SELECT id FROM invoices WHERE venue_id = $1 AND period_start = $2",
        venue_id, period_start,
    )
    if not invoice_row:
        # No billable activity this month — nothing to finalize.
        return None

    invoice_id = invoice_row["id"]
    await conn.execute(
        "UPDATE invoices SET is_final = TRUE WHERE id = $1",
        invoice_id,
    )
    await sync_invoice(conn, invoice_id)
    return str(invoice_id)


async def archive_payment_methods(conn, venue_id: str) -> int:
    """Stub: mark all active payment methods for this venue as 'archived'.

    No real Stripe call. Returns count of archived rows.
    """
    result = await conn.execute(
        "UPDATE payment_methods SET status = 'archived' WHERE venue_id = $1 AND status = 'active'",
        venue_id,
    )
    # asyncpg returns 'UPDATE N' as a string.
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
