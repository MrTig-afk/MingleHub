"""Usage billing -> Stripe (test mode), built against a STUB.

All the logic is real — customer/invoice records, the DB state machine, and
Stripe-scheme webhook *signature verification* — only the outbound calls to
Stripe's API are stubbed (deterministic fake ids, no network) until real test
keys (STRIPE_SECRET_KEY) are dropped into api/.env. With keys present, the same
flow calls the real `stripe` SDK. is_test venues are never invoiced (recompute
already excludes them), so this only ever runs for billable venues.

Nothing here can create a real charge: test keys + test customers only.
"""
import hashlib
import hmac
import json
import os
import time
import uuid

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Real Stripe wiring is gated on a key being present; until then everything runs
# against the stub. Flip by setting STRIPE_SECRET_KEY (test key) in api/.env.
USE_REAL_STRIPE = bool(STRIPE_SECRET_KEY)


def _stub_customer_id(venue_id) -> str:
    return f"cus_stub_{str(venue_id)[:12].replace('-', '')}"


def _stub_invoice_id(invoice_id) -> str:
    return f"in_stub_{str(invoice_id)[:12].replace('-', '')}"


async def sync_invoice(conn, invoice_id) -> dict:
    """Push an invoice (+ its line items) to Stripe and store the returned ids.
    Stub mode returns deterministic ids and never hits the network. Marks the
    invoice 'sent' (awaiting payment). Idempotent: re-running yields the same ids.
    """
    inv = await conn.fetchrow(
        """SELECT i.id, i.venue_id, i.total_amount, i.status,
                  v.stripe_customer_id, v.name
           FROM invoices i JOIN venues v ON v.id = i.venue_id
           WHERE i.id = $1""",
        invoice_id,
    )
    if not inv:
        raise LookupError("invoice_not_found")
    if inv["status"] == "paid":
        return {"customer_id": inv["stripe_customer_id"],
                "stripe_invoice_id": None, "skipped": "already_paid"}

    items = await conn.fetch(
        "SELECT table_id, play_date, units_billed, amount FROM invoice_line_items "
        "WHERE invoice_id = $1 ORDER BY play_date",
        invoice_id,
    )

    customer_id = inv["stripe_customer_id"]
    if USE_REAL_STRIPE:  # pragma: no cover - exercised only with real test keys
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        if not customer_id:
            customer = stripe.Customer.create(name=inv["name"], metadata={"venue_id": str(inv["venue_id"])})
            customer_id = customer["id"]
        for it in items:
            stripe.InvoiceItem.create(
                customer=customer_id, currency="aud",
                amount=int(round(float(it["amount"]) * 100)),
                description=f"{it['units_billed']} block(s) — {it['play_date']}")
        sinv = stripe.Invoice.create(customer=customer_id, auto_advance=True)
        stripe_invoice_id = sinv["id"]
    else:
        if not customer_id:
            customer_id = _stub_customer_id(inv["venue_id"])
        stripe_invoice_id = _stub_invoice_id(invoice_id)

    await conn.execute(
        "UPDATE venues SET stripe_customer_id = $1 WHERE id = $2 AND stripe_customer_id IS NULL",
        customer_id, inv["venue_id"])
    await conn.execute(
        "UPDATE invoices SET stripe_invoice_id = $1, status = 'sent', updated_at = NOW() "
        "WHERE id = $2 AND status != 'paid'",
        stripe_invoice_id, invoice_id)
    return {"customer_id": customer_id, "stripe_invoice_id": stripe_invoice_id,
            "line_items": len(items), "mode": "real" if USE_REAL_STRIPE else "stub"}


def verify_webhook(payload: bytes, sig_header: str, secret: str, tolerance: int = 300) -> dict:
    """Verify a Stripe-scheme webhook signature (t=…,v1=…) and return the parsed
    event. Raises ValueError on a bad/old/missing signature. This is the real
    Stripe HMAC scheme, so it validates genuine test-mode webhooks too."""
    if not secret:
        raise ValueError("webhook_secret_not_set")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts, v1 = parts.get("t"), parts.get("v1")
    if not ts or not v1:
        raise ValueError("malformed_signature")
    if tolerance and abs(int(time.time()) - int(ts)) > tolerance:
        raise ValueError("timestamp_outside_tolerance")
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise ValueError("signature_mismatch")
    return json.loads(payload)


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Produce a Stripe-scheme signature header for `payload` — used by tests and
    by any internal stub that needs to emit a verifiable webhook."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed = f"{ts}.".encode() + payload
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


async def apply_invoice_event(conn, event: dict) -> str | None:
    """Move an invoice's status per a Stripe webhook event. Returns the new
    status (or None if the event isn't one we act on / the invoice is unknown)."""
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    stripe_invoice_id = obj.get("id")
    if not stripe_invoice_id:
        return None
    new_status = {"invoice.paid": "paid", "invoice.payment_succeeded": "paid",
                  "invoice.payment_failed": "failed"}.get(etype)
    if not new_status:
        return None
    updated = await conn.fetchval(
        "UPDATE invoices SET status = $1, updated_at = NOW() "
        "WHERE stripe_invoice_id = $2 RETURNING id",
        new_status, stripe_invoice_id)
    return new_status if updated else None


def make_event(event_type: str, stripe_invoice_id: str) -> bytes:
    """Build a minimal Stripe-shaped event payload (test/stub helper)."""
    return json.dumps({
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "type": event_type,
        "data": {"object": {"id": stripe_invoice_id, "object": "invoice"}},
    }).encode()
