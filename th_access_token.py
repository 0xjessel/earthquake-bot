#!/usr/bin/env python3
"""Refresh the long-lived Threads access token stored in .env.local.

Meant to run DAILY from cron via th_access_token.sh. Long-lived Threads tokens
last 60 days and can only be refreshed while still valid, so a single failed
refresh must never be fatal: running daily gives ~60 chances before expiry, and
any failure exits 1 so cron_watchdog alerts after a few consecutive failures.

Behavior:
  * The refreshed token is written back into .env.local (atomic replace).
  * Expiry is recorded in .token_state.json (read by cron_watchdog's expiry check).
  * A token not seen before (newly pasted) is validated with a cheap /me call;
    an invalid/expired one exits 1 and gets no expiry estimate.
  * Skips (exit 0) if this token was refreshed < MIN_REFRESH_INTERVAL ago, or if
    it is a newly pasted token < 24h old (Threads rejects refreshing those).
  * --force refreshes regardless (e.g. right after repairing the server/venv).

Exit codes: 0 = refreshed or intentionally skipped; 1 = any failure.
Never prints token values.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests
from dotenv import dotenv_values

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env.local")
STATE_PATH = os.path.join(HERE, ".token_state.json")

REFRESH_URL = "https://graph.threads.net/refresh_access_token"
ME_URL = "https://graph.threads.net/v1.0/me"
TOKEN_LIFETIME = 60 * 86400          # documented long-lived token lifetime
NEW_TOKEN_MIN_AGE = 24 * 3600        # Threads won't refresh tokens younger than 24h
MIN_REFRESH_INTERVAL = 20 * 3600     # don't re-refresh more than ~daily
ATTEMPTS = 4
BACKOFF = (5, 15, 45)                # seconds between attempts
TIMEOUT = 20


def log(msg):
    print(msg, flush=True)


def fmt(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def token_id(token):
    """Short, non-reversible fingerprint so state can tell when the token changed."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def load_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log(f"warn: unreadable {os.path.basename(STATE_PATH)} ({e}); starting fresh")
        return {}


def write_atomic(path, text, mode):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(state):
    write_atomic(STATE_PATH, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)


def save_token(new_token):
    """Replace the THREADS_ACCESS_TOKEN line in .env.local, keeping everything else."""
    with open(ENV_PATH) as fh:
        text = fh.read()
    pattern = re.compile(r"^(\s*THREADS_ACCESS_TOKEN\s*=).*$", re.MULTILINE)
    if not pattern.search(text):
        raise RuntimeError("THREADS_ACCESS_TOKEN line not found in .env.local")
    text = pattern.sub(lambda m: m.group(1) + new_token, text, count=1)
    write_atomic(ENV_PATH, text, os.stat(ENV_PATH).st_mode & 0o777)


def api_error(resp):
    """Return (code, message) from a Graph API error body, if any."""
    try:
        err = resp.json().get("error", {})
        return err.get("code"), err.get("message", "")[:200]
    except Exception:
        return None, resp.text[:200]


def call(url, params):
    """GET with retries on network errors / 5xx / 429. Returns the final Response.

    Raises requests.RequestException if every attempt failed at the network level.
    """
    last_exc = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code < 500 and resp.status_code != 429:
                return resp
            log(f"attempt {attempt}/{ATTEMPTS}: HTTP {resp.status_code}")
            last_exc = None
            if attempt == ATTEMPTS:
                return resp
        except requests.RequestException as e:
            # str(e) can include the URL; params carry the token, so log the type only.
            log(f"attempt {attempt}/{ATTEMPTS}: {type(e).__name__}")
            last_exc = e
        if attempt < ATTEMPTS:
            time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
    raise last_exc


def validate(token):
    """Cheap validity check for a token we can't refresh yet."""
    resp = call(ME_URL, {"fields": "id,username", "access_token": token})
    if resp.ok:
        return resp.json().get("username", "?")
    code, msg = api_error(resp)
    raise RuntimeError(f"token is invalid (HTTP {resp.status_code}, code {code}): {msg}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="refresh now even if refreshed recently / token looks new")
    args = ap.parse_args()

    token = (dotenv_values(ENV_PATH).get("THREADS_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(f"THREADS_ACCESS_TOKEN missing from {ENV_PATH}")

    now = int(time.time())
    state = load_state()
    tid = token_id(token)

    if state.get("token_id") != tid:
        # A token we haven't seen: newly pasted by hand (or state was lost).
        # Validate it first (raises if expired/invalid) so we never record an
        # expiry estimate for a dead token. Its true issue time is unknown, so
        # assume "now" for the estimate.
        user = validate(token)
        state = {"token_id": tid, "first_seen": now, "refreshed_at": None,
                 "expires_at": now + TOKEN_LIFETIME, "expires_at_source": "estimated"}
        save_state(state)
        log(f"new token detected (valid for @{user}); estimated expiry {fmt(state['expires_at'])}")

    if not args.force:
        last = state.get("refreshed_at")
        if last and now - last < MIN_REFRESH_INTERVAL:
            log(f"skip: refreshed {(now - last) / 3600:.1f}h ago; "
                f"expires {fmt(state['expires_at'])}")
            return
        if not last and now - state["first_seen"] < NEW_TOKEN_MIN_AGE:
            log(f"skip: token first seen {(now - state['first_seen']) / 3600:.1f}h ago "
                f"(Threads requires 24h before refresh); estimated expiry {fmt(state['expires_at'])}")
            return

    resp = call(REFRESH_URL, {"grant_type": "th_refresh_token", "access_token": token})
    if not resp.ok:
        code, msg = api_error(resp)
        hint = ""
        if code == 190:
            hint = (" -- token is expired/invalid and can no longer be refreshed; "
                    "generate a new long-lived token and put it in .env.local")
        raise RuntimeError(f"refresh failed (HTTP {resp.status_code}, code {code}): {msg}{hint}")

    data = resp.json()
    new_token = (data.get("access_token") or "").strip()
    expires_in = int(data.get("expires_in") or TOKEN_LIFETIME)
    if not new_token:
        raise RuntimeError("refresh response had no access_token")

    if new_token != token:
        save_token(new_token)
    state = {"token_id": token_id(new_token), "first_seen": state.get("first_seen", now),
             "refreshed_at": now, "expires_at": now + expires_in,
             "expires_at_source": "refresh_response"}
    save_state(state)
    changed = "new token saved to .env.local" if new_token != token else "same token string"
    log(f"refreshed OK ({changed}); expires {fmt(state['expires_at'])} "
        f"({expires_in // 86400} days)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Single final line is what th_access_token.sh records for the watchdog.
        # requests exceptions embed the URL (which carries the token) -> redact.
        msg = re.sub(r"(access_token=)[^&\s'\"]+", r"\1***REDACTED***", str(e))
        log(f"ERROR: {type(e).__name__}: {msg}")
        sys.exit(1)
