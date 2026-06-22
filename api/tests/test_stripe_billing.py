"""Tests for the Stripe usage-billing integration (stub mode + real HMAC webhook
verification). No real Stripe calls — deterministic stub + signature math."""
import asyncio
import os
import time
import uuid

import asyncpg

from api.dev_fixtures import OWNER_A_CLERK_ID, STAFF_A_CLERK_ID, VENUE_A_ID
from api.services import stripe_service
from api.tests.conftest import dev_login

SECRET = "whsec_test_secret"


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _run(fn):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_q())


# --- webhook signature (pure) ---

def test_verify_webhook_roundtrip():
    payload = stripe_service.make_event("invoice.paid", "in_test_123")
    sig = stripe_service.sign_payload(payload, SECRET)
    event = stripe_service.verify_webhook(payload, sig, SECRET)
    assert event["type"] == "invoice.paid"
    assert event["data"]["object"]["id"] == "in_test_123"


def test_verify_webhook_bad_signature():
    payload = stripe_service.make_event("invoice.paid", "in_test_123")
    sig = stripe_service.sign_payload(payload, "wrong_secret")
    try:
        stripe_service.verify_webhook(payload, sig, SECRET)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_verify_webhook_old_timestamp_rejected():
    payload = stripe_service.make_event("invoice.paid", "in_test_123")
    sig = stripe_service.sign_payload(payload, SECRET, timestamp=int(time.time()) - 10000)
    try:
        stripe_service.verify_webhook(payload, sig, SECRET)
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- DB-backed: sync + apply event ---

def _make_invoice(stripe_invoice_id=None, status="pending"):
    iid = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            """INSERT INTO invoices (id, venue_id, period_start, period_end, total_amount,
                                     stripe_invoice_id, status)
               VALUES ($1,$2,'2026-06-01','2026-06-30', 12.00, $3, $4)""",
            iid, VENUE_A_ID, stripe_invoice_id, status)
        await conn.execute(
            """INSERT INTO invoice_line_items (id, invoice_id, table_id, play_date, units_billed, amount, cap_applied)
               VALUES (gen_random_uuid(), $1,
                   (SELECT id FROM tables WHERE venue_id=$2 LIMIT 1), '2026-06-15', 4, 12.00, FALSE)""",
            iid, VENUE_A_ID)
    _run(_q)
    return iid


def _cleanup(invoice_id):
    async def _q(conn):
        await conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = $1", invoice_id)
        await conn.execute("DELETE FROM invoices WHERE id = $1", invoice_id)
        await conn.execute("UPDATE venues SET stripe_customer_id = NULL WHERE id = $1", VENUE_A_ID)
    _run(_q)


def test_sync_invoice_stub_sets_ids_and_status():
    iid = _make_invoice()
    try:
        result = _run(lambda c: stripe_service.sync_invoice(c, iid))
        assert result["stripe_invoice_id"].startswith("in_stub_")
        assert result["customer_id"].startswith("cus_stub_")
        assert result["mode"] == "stub"
        row = _run(lambda c: c.fetchrow(
            "SELECT status, stripe_invoice_id FROM invoices WHERE id=$1", iid))
        assert row["status"] == "sent"
        assert row["stripe_invoice_id"] == result["stripe_invoice_id"]
        cust = _run(lambda c: c.fetchval(
            "SELECT stripe_customer_id FROM venues WHERE id=$1", VENUE_A_ID))
        assert cust == result["customer_id"]
    finally:
        _cleanup(iid)


def test_apply_invoice_event_marks_paid():
    iid = _make_invoice(stripe_invoice_id="in_stub_paidcase", status="sent")
    try:
        event = {"type": "invoice.paid", "data": {"object": {"id": "in_stub_paidcase"}}}
        new = _run(lambda c: stripe_service.apply_invoice_event(c, event))
        assert new == "paid"
        status = _run(lambda c: c.fetchval("SELECT status FROM invoices WHERE id=$1", iid))
        assert status == "paid"
    finally:
        _cleanup(iid)


def test_apply_invoice_event_failed():
    iid = _make_invoice(stripe_invoice_id="in_stub_failcase", status="sent")
    try:
        event = {"type": "invoice.payment_failed", "data": {"object": {"id": "in_stub_failcase"}}}
        new = _run(lambda c: stripe_service.apply_invoice_event(c, event))
        assert new == "failed"
    finally:
        _cleanup(iid)


def test_apply_unknown_event_is_noop():
    event = {"type": "invoice.created", "data": {"object": {"id": "in_nope"}}}
    assert _run(lambda c: stripe_service.apply_invoice_event(c, event)) is None


def test_apply_event_never_regresses_paid_invoice():
    # A late payment_failed after the invoice is already paid must NOT flip it.
    iid = _make_invoice(stripe_invoice_id="in_stub_alreadypaid", status="paid")
    try:
        event = {"type": "invoice.payment_failed", "data": {"object": {"id": "in_stub_alreadypaid"}}}
        new = _run(lambda c: stripe_service.apply_invoice_event(c, event))
        assert new is None                                      # no update applied
        status = _run(lambda c: c.fetchval("SELECT status FROM invoices WHERE id=$1", iid))
        assert status == "paid"                                 # stays paid
    finally:
        _cleanup(iid)


def test_sync_invoice_idempotent_on_sent():
    # Re-syncing a 'sent' invoice must not re-push (would dup items in real mode).
    iid = _make_invoice(stripe_invoice_id="in_stub_already", status="sent")
    try:
        result = _run(lambda c: stripe_service.sync_invoice(c, iid))
        assert result["skipped"] == "already_sent"
        assert result["stripe_invoice_id"] == "in_stub_already"
    finally:
        _cleanup(iid)


# --- endpoints ---

def test_usage_webhook_endpoint(client, api_key_header, monkeypatch):
    monkeypatch.setattr(stripe_service, "STRIPE_WEBHOOK_SECRET", SECRET)
    iid = _make_invoice(stripe_invoice_id="in_stub_hook", status="sent")
    try:
        payload = stripe_service.make_event("invoice.paid", "in_stub_hook")
        sig = stripe_service.sign_payload(payload, SECRET)
        resp = client.post("/api/stripe/usage-webhook", content=payload,
                           headers={**api_key_header, "stripe-signature": sig})
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] == "paid"
        # bad signature -> 400
        bad = client.post("/api/stripe/usage-webhook", content=payload,
                          headers={**api_key_header, "stripe-signature": "t=1,v1=bad"})
        assert bad.status_code == 400
    finally:
        _cleanup(iid)


def test_sync_endpoint_owner_and_staff(client, api_key_header):
    iid = _make_invoice()
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.post("/api/dashboard/billing/sync",
                           headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["stripe_invoice_id"].startswith("in_stub_")
        staff = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
        forbidden = client.post("/api/dashboard/billing/sync",
                                headers={**api_key_header, **auth_header(staff)})
        assert forbidden.status_code == 403
    finally:
        _cleanup(iid)
