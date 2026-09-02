"""Production must never fall back to the dev HMAC token.

api/auth.py has two auth paths: real Clerk JWTs (CLERK_JWKS_URL) and dev-login
HMAC tokens. Before this guard, an unconfigured production silently used the
second one, making SESSION_SECRET the only thing protecting a venue dashboard.

The import checks run in a subprocess so a failed import cannot poison the rest
of the suite's already-imported api.auth.
"""
import os
import subprocess
import sys

import pytest
from fastapi import HTTPException

import api.auth


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_auth_with(env_overrides):
    """Import api.auth in a clean subprocess. Returns (returncode, stderr)."""
    env = {**os.environ, **env_overrides}
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, "-c", "import api.auth"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    return proc.returncode, proc.stderr


def test_production_without_clerk_jwks_refuses_to_boot():
    code, stderr = _import_auth_with({"DEV_MODE": "false", "CLERK_JWKS_URL": None})
    assert code != 0, "api.auth imported cleanly in production with no CLERK_JWKS_URL"
    assert "CLERK_JWKS_URL must be set in production" in stderr


def test_production_with_clerk_jwks_boots():
    code, stderr = _import_auth_with({
        "DEV_MODE": "false",
        "CLERK_JWKS_URL": "https://example.test/.well-known/jwks.json",
    })
    assert code == 0, f"api.auth failed to import with CLERK_JWKS_URL set:\n{stderr}"


def test_dev_mode_without_clerk_jwks_still_boots():
    code, stderr = _import_auth_with({"DEV_MODE": "true", "CLERK_JWKS_URL": None})
    assert code == 0, f"api.auth failed to import in DEV_MODE:\n{stderr}"


def test_hmac_token_is_rejected_outside_dev_mode(monkeypatch):
    """A validly-signed dev token must not authenticate when DEV_MODE is off."""
    token = api.auth.issue_dev_token("user_whatever")
    assert api.auth._verify_token(token) == "user_whatever"  # DEV_MODE on: accepted

    monkeypatch.setenv("DEV_MODE", "false")
    with pytest.raises(HTTPException) as exc:
        api.auth._verify_token(token)
    assert exc.value.status_code == 401
