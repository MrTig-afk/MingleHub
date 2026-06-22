"""Shared dev/test fixture IDs for the platform foundation.

Used by scripts/seed_platform.py to seed deterministic rows, and by the
test suite to authenticate as a known user without re-discovering IDs
from the DB. Dev-only — never referenced by production code paths.
"""
import uuid


def _id(label: str) -> str:
    # Deterministic per label so reseeding is idempotent (same UUID every run).
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"minglehub.dev.{label}"))


VENUE_A_ID = _id("venue-a")
VENUE_B_ID = _id("venue-b")

VENUE_A_TABLE_ID = _id("venue-a-table-1")
VENUE_A_TABLE_2_ID = _id("venue-a-table-2")  # second table — lets tests move a tag between tables
VENUE_B_TABLE_ID = _id("venue-b-table-1")
VENUE_B_TABLE_2_ID = _id("venue-b-table-2")  # second table for The Last Chance

OWNER_A_ID = _id("owner-a")
STAFF_A_ID = _id("staff-a")
OWNER_B_ID = _id("owner-b")
ADMIN_ID = _id("admin")

OWNER_A_CLERK_ID = "dev_owner_a"
STAFF_A_CLERK_ID = "dev_staff_a"
OWNER_B_CLERK_ID = "dev_owner_b"
ADMIN_CLERK_ID = "dev_admin"

# Venue-less owner fixture: a venue_owner with no venue yet (for invite flow tests).
OWNER_NOVEN_ID = _id("owner-no-venue")
OWNER_NOVEN_CLERK_ID = "dev_owner_noven"
