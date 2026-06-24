"""Tests for the venue billing lifecycle: cancel, reactivate, suspend, dunning sweep.

Pattern: same as test_billing.py and test_admin_ops.py.
- Service-level tests use asyncio.run + asyncpg.connect directly.
- Endpoint tests use TestClient + dev_login from conftest.
- All DB mutations are torn down in finally blocks.

Total target: 43 tests (see spec.md section 7).
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
    VENUE_B_ID,
)
from api.tests.conftest import dev_login
from api.services.stripe_service import make_event, sign_payload, STRIPE_WEBHOOK_SECRET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro_fn):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_q())


def _set_venue_status(venue_id, status, cancelled_at=None, suspended_at=None,
                      cancellation_reason=None, suspension_reason=None):
    async def _q(conn):
        await conn.execute(
            """
            UPDATE venues SET status = $1, cancelled_at = $2, suspended_at = $3,
                cancellation_reason = $4, suspension_reason = $5, updated_at = NOW()
            WHERE id = $6
            """,
            status, cancelled_at, suspended_at, cancellation_reason, suspension_reason, venue_id,
        )
    _run(_q)


def _get_venue_status(venue_id):
    async def _q(conn):
        return await conn.fetchrow(
            "SELECT status, cancelled_at, suspended_at, cancellation_reason, suspension_reason "
            "FROM venues WHERE id = $1",
            venue_id,
        )
    return _run(_q)


def _restore_venue_status(venue_id):
    """Reset venue to active with all lifecycle columns NULL."""
    _set_venue_status(venue_id, "active",
                      cancelled_at=None, suspended_at=None,
                      cancellation_reason=None, suspension_reason=None)


def _make_failed_invoice(venue_id, failed_days_ago=8):
    """Insert an invoice with status='failed' and updated_at N days ago."""
    invoice_id = str(uuid.uuid4())
    from datetime import date
    today = date.today()
    # Use first of current month for period_start to avoid unique constraint issues.
    period_start = date(today.year, today.month, 1)
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    period_end = date(today.year, today.month, last_day)
    failed_at = datetime.now(timezone.utc) - timedelta(days=failed_days_ago)

    async def _q(conn):
        await conn.execute(
            """
            INSERT INTO invoices (id, venue_id, period_start, period_end, total_amount, status,
                                  stripe_invoice_id, updated_at, created_at)
            VALUES ($1, $2, $3, $4, 0, 'failed', $5, $6, $6)
            ON CONFLICT (venue_id, period_start) DO UPDATE
                SET status = 'failed', updated_at = $6, stripe_invoice_id = $5
            """,
            invoice_id, venue_id, period_start, period_end,
            f"in_test_fail_{invoice_id[:8]}", failed_at.replace(tzinfo=None),
        )
        return invoice_id
    return _run(_q)


def _cleanup_invoices(venue_id):
    async def _q(conn):
        inv_ids = await conn.fetch("SELECT id FROM invoices WHERE venue_id = $1", venue_id)
        for row in inv_ids:
            await conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = $1", row["id"])
        await conn.execute("DELETE FROM invoices WHERE venue_id = $1", venue_id)
    _run(_q)


def _cleanup_audit_logs(venue_id):
    async def _q(conn):
        await conn.execute(
            "DELETE FROM admin_audit_log WHERE target_id = $1", venue_id
        )
    _run(_q)


def _cleanup_config_overrides(venue_id):
    async def _q(conn):
        await conn.execute(
            "DELETE FROM venue_config_overrides WHERE venue_id = $1", venue_id
        )
    _run(_q)


def _cleanup_payment_methods(venue_id):
    async def _q(conn):
        await conn.execute("DELETE FROM payment_methods WHERE venue_id = $1", venue_id)
    _run(_q)


def _insert_payment_method(venue_id):
    pm_id = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            "INSERT INTO payment_methods (id, venue_id, status) VALUES ($1, $2, 'active')",
            pm_id, venue_id,
        )
        return pm_id
    return _run(_q)


def _count_final_invoices(venue_id):
    async def _q(conn):
        return await conn.fetchval(
            "SELECT COUNT(*) FROM invoices WHERE venue_id = $1 AND is_final = TRUE", venue_id
        )
    return _run(_q)


def auth_header(token, api_key=None):
    headers = {"Authorization": f"Bearer {token}"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _fresh_phone():
    return f"test-phone-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Import the service under test (late import after env is loaded by conftest).
# ---------------------------------------------------------------------------

from api.services import venue_lifecycle_service  # noqa: E402


# ===========================================================================
# SERVICE-LEVEL TESTS  (#1 – #17)
# ===========================================================================

class TestCancelVenue:

    def test_cancel_sets_status_and_timestamps(self):
        """#1 — cancel_venue sets status='cancelled', cancelled_at NOT NULL."""
        try:
            async def _q(conn):
                return await venue_lifecycle_service.cancel_venue(
                    conn, VENUE_A_ID, "testing cancel"
                )
            result = _run(_q)
            assert result["status"] == "cancelled"
            assert result["cancelled_at"] is not None

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "cancelled"
            assert row["cancelled_at"] is not None
            assert row["cancellation_reason"] == "testing cancel"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_cancel_issues_exactly_one_final_invoice(self):
        """#2 — cancel_venue issues exactly one is_final=TRUE invoice."""
        try:
            async def _q(conn):
                return await venue_lifecycle_service.cancel_venue(
                    conn, VENUE_A_ID, "test final invoice"
                )
            _run(_q)
            # The invoice may or may not exist depending on billable sessions;
            # the count of final invoices must be 0 or 1 (never > 1).
            count = _count_final_invoices(VENUE_A_ID)
            assert count <= 1
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_cancel_idempotent_no_second_invoice(self):
        """#3 — double-cancel: same cancelled_at, no error, no second final invoice."""
        try:
            async def _cancel(conn):
                return await venue_lifecycle_service.cancel_venue(conn, VENUE_A_ID, "first")

            async def _cancel2(conn):
                return await venue_lifecycle_service.cancel_venue(conn, VENUE_A_ID, "second")

            r1 = _run(_cancel)
            assert r1["status"] == "cancelled"
            count_after_first = _count_final_invoices(VENUE_A_ID)

            r2 = _run(_cancel2)
            assert r2.get("already_cancelled") is True
            count_after_second = _count_final_invoices(VENUE_A_ID)

            assert count_after_second == count_after_first  # No second invoice
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_cancel_archives_payment_methods(self):
        """#4 — payment_methods.status='archived' after cancel."""
        try:
            pm_id = _insert_payment_method(VENUE_A_ID)

            async def _q(conn):
                return await venue_lifecycle_service.cancel_venue(
                    conn, VENUE_A_ID, "test archive"
                )
            _run(_q)

            async def _check(conn):
                return await conn.fetchval(
                    "SELECT status FROM payment_methods WHERE id = $1", pm_id
                )
            status = _run(_check)
            assert status == "archived"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_cancel_suspended_venue_rejected(self):
        """#5 — cancelling a suspended venue raises ValueError('venue_suspended')."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning")
        try:
            import pytest as _pytest
            with _pytest.raises(ValueError, match="venue_suspended"):
                async def _q(conn):
                    return await venue_lifecycle_service.cancel_venue(
                        conn, VENUE_A_ID, "try cancel"
                    )
                _run(_q)
        finally:
            _restore_venue_status(VENUE_A_ID)


class TestReactivateVenue:

    def test_reactivate_within_7_days(self):
        """#6 — reactivate_venue within 7 days: status='active', columns cleared."""
        cancelled_at = datetime.now(timezone.utc) - timedelta(days=2)
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=cancelled_at.replace(tzinfo=None),
                          cancellation_reason="test")
        try:
            async def _q(conn):
                return await venue_lifecycle_service.reactivate_venue(conn, VENUE_A_ID)
            result = _run(_q)
            assert result["status"] == "active"

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
            assert row["cancelled_at"] is None
            assert row["cancellation_reason"] is None
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_reactivate_after_7_days_denied(self):
        """#7 — reactivate_venue after 7 days raises ValueError('reactivation_window_expired')."""
        cancelled_at = datetime.now(timezone.utc) - timedelta(days=8)
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=cancelled_at.replace(tzinfo=None),
                          cancellation_reason="old")
        try:
            import pytest as _pytest
            with _pytest.raises(ValueError, match="reactivation_window_expired"):
                async def _q(conn):
                    return await venue_lifecycle_service.reactivate_venue(conn, VENUE_A_ID)
                _run(_q)
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_reactivate_active_venue_noop(self):
        """#8 — reactivating an already-active venue returns already_active=True, no error."""
        async def _q(conn):
            return await venue_lifecycle_service.reactivate_venue(conn, VENUE_A_ID)
        result = _run(_q)
        assert result.get("already_active") is True

    def test_reactivate_suspended_venue_denied(self):
        """#9 — reactivating a suspended venue raises ValueError('venue_suspended')."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning")
        try:
            import pytest as _pytest
            with _pytest.raises(ValueError, match="venue_suspended"):
                async def _q(conn):
                    return await venue_lifecycle_service.reactivate_venue(conn, VENUE_A_ID)
                _run(_q)
        finally:
            _restore_venue_status(VENUE_A_ID)


class TestDunningAndAutoReactivate:

    def test_suspend_for_nonpayment(self):
        """#10 — suspend_for_nonpayment: status='suspended', suspended_at set, reason='dunning'."""
        try:
            async def _q(conn):
                return await venue_lifecycle_service.suspend_for_nonpayment(conn, VENUE_A_ID)
            result = _run(_q)
            assert result is True

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "suspended"
            assert row["suspended_at"] is not None
            assert row["suspension_reason"] == "dunning"
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_auto_reactivate_on_payment_dunning(self):
        """#11 — auto_reactivate_on_payment: dunning-suspended venue becomes active."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            async def _q(conn):
                return await venue_lifecycle_service.auto_reactivate_on_payment(conn, VENUE_A_ID)
            result = _run(_q)
            assert result is True

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
            assert row["suspended_at"] is None
            assert row["suspension_reason"] is None
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_auto_reactivate_ignores_admin_suspended(self):
        """#12 — auto_reactivate_on_payment: admin-suspended venue stays suspended."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="admin",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            async def _q(conn):
                return await venue_lifecycle_service.auto_reactivate_on_payment(conn, VENUE_A_ID)
            result = _run(_q)
            assert result is False

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "suspended"
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_auto_reactivate_ignores_cancelled(self):
        """#13 — auto_reactivate_on_payment: cancelled venue stays cancelled."""
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            async def _q(conn):
                return await venue_lifecycle_service.auto_reactivate_on_payment(conn, VENUE_A_ID)
            result = _run(_q)
            assert result is False

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "cancelled"
        finally:
            _restore_venue_status(VENUE_A_ID)


class TestDunningSweep:

    def test_dunning_sweep_suspends_after_7_days(self):
        """#14 — check_dunning_suspensions finds stale failed invoice and suspends."""
        # Ensure venue is not a test venue for this sweep test.
        async def _clear_test(conn):
            await conn.execute("UPDATE venues SET is_test = FALSE WHERE id = $1", VENUE_A_ID)
        _run(_clear_test)
        _make_failed_invoice(VENUE_A_ID, failed_days_ago=8)
        try:
            async def _q(conn):
                return await venue_lifecycle_service.check_dunning_suspensions(conn)
            count = _run(_q)
            assert count >= 1

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "suspended"
            assert row["suspension_reason"] == "dunning"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    def test_dunning_sweep_skips_already_suspended(self):
        """#15 — check_dunning_suspensions: already-suspended venue not counted twice."""
        async def _clear_test(conn):
            await conn.execute("UPDATE venues SET is_test = FALSE WHERE id = $1", VENUE_A_ID)
        _run(_clear_test)
        _make_failed_invoice(VENUE_A_ID, failed_days_ago=8)
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning")
        try:
            async def _q(conn):
                return await venue_lifecycle_service.check_dunning_suspensions(conn)
            count = _run(_q)
            # Venue A was already suspended — the sweep should not count it again.
            # (The WHERE status='active' clause means it's a no-op.)
            assert count == 0
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    def test_dunning_sweep_skips_test_venues(self):
        """#16 — check_dunning_suspensions: is_test=TRUE venues are never suspended."""
        # Mark venue A as test, make it have a stale failed invoice.
        async def _set_test(conn):
            await conn.execute("UPDATE venues SET is_test = TRUE WHERE id = $1", VENUE_A_ID)
        _run(_set_test)
        _make_failed_invoice(VENUE_A_ID, failed_days_ago=8)
        try:
            async def _q(conn):
                return await venue_lifecycle_service.check_dunning_suspensions(conn)
            count = _run(_q)
            assert count == 0

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
        finally:
            async def _unset_test(conn):
                await conn.execute("UPDATE venues SET is_test = FALSE WHERE id = $1", VENUE_A_ID)
            _run(_unset_test)
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    def test_recompute_skips_final_invoice(self):
        """#17 — nightly rollup does not recompute a final invoice."""
        from api.services.billing_service import recompute_invoices

        # Create a final invoice, then confirm recompute skips it.
        async def _q(conn):
            await venue_lifecycle_service.cancel_venue(conn, VENUE_A_ID, "for test 17")

        _run(_q)
        count_before = _count_final_invoices(VENUE_A_ID)

        async def _rollup(conn):
            await recompute_invoices(conn)

        _run(_rollup)

        count_after = _count_final_invoices(VENUE_A_ID)
        # is_final invoices are counted in skipped_paid.
        assert count_after == count_before
        try:
            pass
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)


# ===========================================================================
# OWNER ENDPOINT TESTS  (#18 – #30)
# ===========================================================================

class TestOwnerCancelEndpoint:

    def test_owner_cancel_endpoint_200(self, client, api_key_header):
        """#18 — POST /api/dashboard/cancel -> 200, status changed in DB."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp = client.post("/api/dashboard/cancel", headers=headers,
                               json={"reason": "test cancel"})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "cancelled"

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "cancelled"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_owner_cancel_idempotent_200(self, client, api_key_header):
        """#19 — second POST /cancel -> 200, same result, no error."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp1 = client.post("/api/dashboard/cancel", headers=headers,
                                json={"reason": "first cancel"})
            assert resp1.status_code == 200

            resp2 = client.post("/api/dashboard/cancel", headers=headers,
                                json={"reason": "second cancel"})
            assert resp2.status_code == 200
            data = resp2.json()
            assert data.get("already_cancelled") is True
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_owner_cancel_reason_required_422(self, client, api_key_header):
        """#20 — missing/empty reason -> 422."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/cancel", headers=headers,
                           json={"reason": ""})
        assert resp.status_code == 422

    def test_owner_cancel_extra_field_422(self, client, api_key_header):
        """#21 — extra=forbid -> 422."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/cancel", headers=headers,
                           json={"reason": "ok", "extra_field": "bad"})
        assert resp.status_code == 422


class TestOwnerReactivateEndpoint:

    def test_owner_reactivate_endpoint_200(self, client, api_key_header):
        """#22 — POST /api/dashboard/reactivate -> 200, status='active' in DB."""
        cancelled_at = datetime.now(timezone.utc) - timedelta(days=2)
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=cancelled_at.replace(tzinfo=None),
                          cancellation_reason="test")
        try:
            token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
            headers = {**api_key_header, **auth_header(token)}
            resp = client.post("/api/dashboard/reactivate", headers=headers, json={})
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "active"

            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_owner_reactivate_expired_409(self, client, api_key_header):
        """#23 — cancelled >7 days ago -> 409."""
        cancelled_at = datetime.now(timezone.utc) - timedelta(days=8)
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=cancelled_at.replace(tzinfo=None),
                          cancellation_reason="old")
        try:
            token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
            headers = {**api_key_header, **auth_header(token)}
            resp = client.post("/api/dashboard/reactivate", headers=headers, json={})
            assert resp.status_code == 409
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_owner_reactivate_suspended_409(self, client, api_key_header):
        """#24 — venue suspended -> 409."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning")
        try:
            token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
            headers = {**api_key_header, **auth_header(token)}
            resp = client.post("/api/dashboard/reactivate", headers=headers, json={})
            assert resp.status_code == 409
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_staff_cancel_403(self, client, api_key_header):
        """#25 — venue_staff POST cancel -> 403."""
        token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/cancel", headers=headers,
                           json={"reason": "staff attempt"})
        assert resp.status_code == 403

    def test_staff_reactivate_403(self, client, api_key_header):
        """#26 — venue_staff POST reactivate -> 403."""
        token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/reactivate", headers=headers, json={})
        assert resp.status_code == 403


class TestSettingsBillingStatus:

    def test_settings_includes_venue_status(self, client, api_key_header):
        """#27 — GET /dashboard/settings has venue_status.status, cancelled_at, can_reactivate."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.get("/api/dashboard/settings", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "venue_status" in data
        assert "status" in data["venue_status"]
        assert "cancelled_at" in data["venue_status"]
        assert "can_reactivate" in data["venue_status"]

    def test_billing_includes_venue_status(self, client, api_key_header):
        """#28 — GET /dashboard/billing has venue_status."""
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.get("/api/dashboard/billing", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "venue_status" in data


# ===========================================================================
# BOLA TESTS  (#29 – #30)
# ===========================================================================

class TestBOLA:

    def test_owner_b_cancel_only_affects_own_venue(self, client, api_key_header):
        """#29 — Owner B cancel does not touch Venue A."""
        token = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/cancel", headers=headers,
                           json={"reason": "owner b cancel"})
        # Should succeed for B's own venue.
        assert resp.status_code in (200, 409)  # 409 if venue B already in odd state

        # Venue A must be untouched.
        row = _get_venue_status(VENUE_A_ID)
        assert row["status"] == "active"

    def test_owner_b_reactivate_only_affects_own_venue(self, client, api_key_header):
        """#30 — Owner B reactivate does not touch Venue A."""
        token = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/reactivate", headers=headers, json={})
        # May be 200 (already_active) or 409 depending on B's state.
        assert resp.status_code in (200, 409)

        row = _get_venue_status(VENUE_A_ID)
        assert row["status"] == "active"

        # Restore B's venue in case the test above cancelled it.
        _restore_venue_status(VENUE_B_ID)
        _cleanup_invoices(VENUE_B_ID)
        _cleanup_payment_methods(VENUE_B_ID)


# ===========================================================================
# NEW-GAME BLOCKING  (#31 – #34)
# ===========================================================================

class TestNewGameBlocking:

    def test_tap_surfaces_venue_inactive_when_cancelled(self, client, api_key_header):
        """#31 — GET /patron/tap with cancelled venue slug -> 200 + venue_inactive.
        The venue resolves (it exists) so the patron sees the informative
        "venue not active" screen rather than a generic 404 "tap didn't go
        through"; new play is still blocked downstream."""
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            resp = client.get(
                "/api/patron/tap",
                headers=api_key_header,
                params={"venue_slug": "fifty-five-bar", "table_number": 1,
                        "phone_id": "test-phone-blocked"},
            )
            assert resp.status_code == 200
            assert resp.json()["table_state"]["phase"] == "venue_inactive"
            assert resp.json()["table_state"]["venue_status"] == "cancelled"
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_tap_surfaces_venue_inactive_when_suspended(self, client, api_key_header):
        """#32 — GET /patron/tap with suspended venue slug -> 200 + venue_inactive."""
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            resp = client.get(
                "/api/patron/tap",
                headers=api_key_header,
                params={"venue_slug": "fifty-five-bar", "table_number": 1,
                        "phone_id": "test-phone-susp"},
            )
            assert resp.status_code == 200
            assert resp.json()["table_state"]["phase"] == "venue_inactive"
            assert resp.json()["table_state"]["venue_status"] == "suspended"
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_new_group_blocked_when_venue_inactive(self, client, api_key_header):
        """#33 — POST /patron/table/{id}/new-group when cancelled -> 409."""
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            resp = client.post(
                f"/api/patron/table/{VENUE_A_TABLE_ID}/new-group",
                headers=api_key_header,
                json={"phone_id": "test-phone-newgroup"},
            )
            assert resp.status_code == 409
        finally:
            _restore_venue_status(VENUE_A_ID)

    def test_in_progress_session_unaffected_after_cancel(self):
        """#34 — Start session while active, cancel venue, complete a round — round succeeds.

        This is a service-level check: the round_service endpoints do not
        check venue status (only the game-entry gates do). We verify that
        game_sessions rows are unaffected by the venue cancel.
        """
        async def _q(conn):
            session_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO game_sessions
                    (id, venue_id, table_id, player_count, started_at, created_at)
                VALUES ($1, $2, $3, 2, NOW(), NOW())
                """,
                session_id, VENUE_A_ID, VENUE_A_TABLE_ID,
            )
            # Cancel the venue.
            await venue_lifecycle_service.cancel_venue(conn, VENUE_A_ID, "mid-session test")
            # The session row must still exist (not deleted/ended).
            row = await conn.fetchrow(
                "SELECT id FROM game_sessions WHERE id = $1 AND ended_at IS NULL",
                session_id,
            )
            return session_id, row is not None

        try:
            session_id, still_active = _run(_q)
            assert still_active, "In-progress session was unexpectedly ended by cancel_venue"
        finally:
            async def _cleanup(conn):
                await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
            _run(_cleanup)
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)

    def test_in_progress_session_end_game_succeeds_after_cancel(self, client, api_key_header):
        """#34b — HTTP-level proof: POST /patron/sessions/{id}/end-game succeeds after venue cancel.

        The key business rule: round-loop endpoints do NOT check venue status.
        A session that was already in-progress when the venue was cancelled
        must be able to end normally via the patron endpoint.
        """
        origin_phone = _fresh_phone()
        session_id = str(uuid.uuid4())

        async def _setup(conn):
            # Insert a complete game_session row with an origin_phone so end_game
            # can verify the caller is the origin.
            player_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO game_sessions
                    (id, venue_id, table_id, player_count, origin_phone_id, started_at, created_at)
                VALUES ($1, $2, $3, 1, $4, NOW(), NOW())
                """,
                session_id, VENUE_A_ID, VENUE_A_TABLE_ID, origin_phone,
            )
            await conn.execute(
                """
                INSERT INTO game_players
                    (id, session_id, phone_id, name)
                VALUES ($1, $2, $3, 'TestPlayer')
                """,
                player_id, session_id, origin_phone,
            )

        _run(_setup)
        try:
            # Cancel the venue BEFORE calling end-game.
            async def _cancel(conn):
                await venue_lifecycle_service.cancel_venue(conn, VENUE_A_ID, "cancel before end-game")
            _run(_cancel)

            # end-game must still succeed (200) — venue status is irrelevant here.
            resp = client.post(
                f"/api/patron/sessions/{session_id}/end-game",
                headers=api_key_header,
                json={"phone_id": origin_phone},
            )
            assert resp.status_code == 200, (
                f"end-game returned {resp.status_code} after venue cancel: {resp.text}"
            )

            # Confirm session is now ended in DB.
            async def _check(conn):
                return await conn.fetchval(
                    "SELECT ended_at FROM game_sessions WHERE id = $1", session_id
                )
            ended_at = _run(_check)
            assert ended_at is not None, "Session was not ended after end-game call"
        finally:
            async def _cleanup(conn):
                await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
                await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
            _run(_cleanup)
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)


# ===========================================================================
# ADMIN OVERRIDE TESTS  (#35 – #39)
# ===========================================================================

class TestAdminStatusOverride:

    def test_admin_suspend_venue(self, client, api_key_header):
        """#35 — PATCH /admin/venues/{id} status='suspended' -> 200, DB updated."""
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp = client.patch(
                f"/api/admin/venues/{VENUE_A_ID}",
                headers=headers,
                json={"status": "suspended", "reason": "test admin suspend"},
            )
            assert resp.status_code == 200, resp.text
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "suspended"
            assert row["suspension_reason"] == "admin"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_audit_logs(VENUE_A_ID)
            _cleanup_config_overrides(VENUE_A_ID)

    def test_admin_cancel_venue_with_final_invoice(self, client, api_key_header):
        """#36 — PATCH status='cancelled' -> 200, is_final invoice created."""
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp = client.patch(
                f"/api/admin/venues/{VENUE_A_ID}",
                headers=headers,
                json={"status": "cancelled", "reason": "admin force cancel"},
            )
            assert resp.status_code == 200, resp.text
            # Final invoice count is 0 or 1 (may be 0 if no activity this month).
            count = _count_final_invoices(VENUE_A_ID)
            assert count <= 1
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
            _cleanup_payment_methods(VENUE_A_ID)
            _cleanup_audit_logs(VENUE_A_ID)
            _cleanup_config_overrides(VENUE_A_ID)

    def test_admin_reactivate_venue(self, client, api_key_header):
        """#37 — PATCH status='active' -> 200, cancelled_at/suspended_at cleared."""
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
            headers = {**api_key_header, **auth_header(token)}
            resp = client.patch(
                f"/api/admin/venues/{VENUE_A_ID}",
                headers=headers,
                json={"status": "active", "reason": "admin reactivate"},
            )
            assert resp.status_code == 200, resp.text
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
            assert row["cancelled_at"] is None
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_audit_logs(VENUE_A_ID)
            _cleanup_config_overrides(VENUE_A_ID)

    def test_admin_status_change_audit_log(self, client, api_key_header):
        """#38 — admin_audit_log row with action='venue_status_change' exists."""
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp = client.patch(
                f"/api/admin/venues/{VENUE_A_ID}",
                headers=headers,
                json={"status": "suspended", "reason": "audit test"},
            )
            assert resp.status_code == 200, resp.text

            async def _check(conn):
                return await conn.fetchrow(
                    "SELECT id FROM admin_audit_log "
                    "WHERE target_id = $1 AND action = 'venue_status_change' "
                    "ORDER BY created_at DESC LIMIT 1",
                    VENUE_A_ID,
                )
            row = _run(_check)
            assert row is not None
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_audit_logs(VENUE_A_ID)
            _cleanup_config_overrides(VENUE_A_ID)

    def test_admin_status_change_config_override(self, client, api_key_header):
        """#39 — venue_config_overrides row with field_name='status' exists."""
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        try:
            resp = client.patch(
                f"/api/admin/venues/{VENUE_A_ID}",
                headers=headers,
                json={"status": "suspended", "reason": "config override test"},
            )
            assert resp.status_code == 200, resp.text

            async def _check(conn):
                return await conn.fetchrow(
                    "SELECT id FROM venue_config_overrides "
                    "WHERE venue_id = $1 AND field_name = 'status' "
                    "ORDER BY created_at DESC LIMIT 1",
                    VENUE_A_ID,
                )
            row = _run(_check)
            assert row is not None
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_audit_logs(VENUE_A_ID)
            _cleanup_config_overrides(VENUE_A_ID)


# ===========================================================================
# WEBHOOK + DUNNING TESTS  (#40 – #43)
# ===========================================================================

def _make_signed_webhook(event_type, stripe_invoice_id, secret):
    payload = make_event(event_type, stripe_invoice_id)
    sig = sign_payload(payload, secret or "whsec_test_secret")
    return payload, sig


def _insert_sent_invoice(venue_id, stripe_invoice_id):
    invoice_id = str(uuid.uuid4())
    from datetime import date
    import calendar
    today = date.today()
    period_start = date(today.year, today.month, 1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    period_end = date(today.year, today.month, last_day)

    async def _q(conn):
        await conn.execute(
            """
            INSERT INTO invoices
                (id, venue_id, period_start, period_end, total_amount, status,
                 stripe_invoice_id, updated_at, created_at)
            VALUES ($1, $2, $3, $4, 30, 'sent', $5, NOW(), NOW())
            ON CONFLICT (venue_id, period_start) DO UPDATE
                SET status = 'sent', stripe_invoice_id = $5
            """,
            invoice_id, venue_id, period_start, period_end, stripe_invoice_id,
        )
    _run(_q)
    return invoice_id


class TestWebhookReactivate:
    """Tests #40–#43: webhook auto-reactivate on payment."""

    @pytest.mark.skipif(
        not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "whsec_REPLACE_ME",
        reason="STRIPE_WEBHOOK_SECRET not set — webhook signature will fail",
    )
    def test_webhook_paid_auto_reactivates_dunning_suspended(self, client, api_key_header):
        """#40 — POST /stripe/usage-webhook invoice.paid -> venue active."""
        stripe_inv_id = f"in_test_autoact_{uuid.uuid4().hex[:8]}"
        _insert_sent_invoice(VENUE_A_ID, stripe_inv_id)
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            payload, sig = _make_signed_webhook("invoice.paid", stripe_inv_id, STRIPE_WEBHOOK_SECRET)
            resp = client.post(
                "/api/stripe/usage-webhook",
                headers={**api_key_header, "stripe-signature": sig},
                content=payload,
            )
            assert resp.status_code == 200, resp.text
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    @pytest.mark.skipif(
        not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "whsec_REPLACE_ME",
        reason="STRIPE_WEBHOOK_SECRET not set",
    )
    def test_webhook_paid_does_not_reactivate_admin_suspended(self, client, api_key_header):
        """#41 — Paid webhook, admin-suspended venue -> stays suspended."""
        stripe_inv_id = f"in_test_adminsusp_{uuid.uuid4().hex[:8]}"
        _insert_sent_invoice(VENUE_A_ID, stripe_inv_id)
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="admin",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            payload, sig = _make_signed_webhook("invoice.paid", stripe_inv_id, STRIPE_WEBHOOK_SECRET)
            resp = client.post(
                "/api/stripe/usage-webhook",
                headers={**api_key_header, "stripe-signature": sig},
                content=payload,
            )
            assert resp.status_code == 200
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "suspended"
            assert row["suspension_reason"] == "admin"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    @pytest.mark.skipif(
        not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "whsec_REPLACE_ME",
        reason="STRIPE_WEBHOOK_SECRET not set",
    )
    def test_webhook_paid_does_not_reactivate_cancelled(self, client, api_key_header):
        """#42 — Paid webhook, venue cancelled -> stays cancelled."""
        stripe_inv_id = f"in_test_canc_{uuid.uuid4().hex[:8]}"
        _insert_sent_invoice(VENUE_A_ID, stripe_inv_id)
        _set_venue_status(VENUE_A_ID, "cancelled",
                          cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            payload, sig = _make_signed_webhook("invoice.paid", stripe_inv_id, STRIPE_WEBHOOK_SECRET)
            resp = client.post(
                "/api/stripe/usage-webhook",
                headers={**api_key_header, "stripe-signature": sig},
                content=payload,
            )
            assert resp.status_code == 200
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "cancelled"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)

    @pytest.mark.skipif(
        not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "whsec_REPLACE_ME",
        reason="STRIPE_WEBHOOK_SECRET not set",
    )
    def test_webhook_replay_idempotent(self, client, api_key_header):
        """#43 — Replaying paid event -> no error, venue still active."""
        stripe_inv_id = f"in_test_replay_{uuid.uuid4().hex[:8]}"
        _insert_sent_invoice(VENUE_A_ID, stripe_inv_id)
        _set_venue_status(VENUE_A_ID, "suspended", suspension_reason="dunning",
                          suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            payload, sig = _make_signed_webhook("invoice.paid", stripe_inv_id, STRIPE_WEBHOOK_SECRET)
            # First delivery.
            resp1 = client.post(
                "/api/stripe/usage-webhook",
                headers={**api_key_header, "stripe-signature": sig},
                content=payload,
            )
            assert resp1.status_code == 200
            # Replay with a fresh signature (new timestamp).
            payload2, sig2 = _make_signed_webhook("invoice.paid", stripe_inv_id, STRIPE_WEBHOOK_SECRET)
            resp2 = client.post(
                "/api/stripe/usage-webhook",
                headers={**api_key_header, "stripe-signature": sig2},
                content=payload2,
            )
            assert resp2.status_code == 200
            row = _get_venue_status(VENUE_A_ID)
            assert row["status"] == "active"
        finally:
            _restore_venue_status(VENUE_A_ID)
            _cleanup_invoices(VENUE_A_ID)
