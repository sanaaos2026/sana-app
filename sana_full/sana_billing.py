"""Minimum Billing Slice for Sana: one offer, coupons, and company subscriptions."""

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import requests

from database_config import acquire_schema_lock


PLAN_ID = "SANA-P0"
DEFAULT_FEATURES = (
    "التقرير الكامل القابل للتنزيل",
    "مراجعة خبير سنع",
    "خطة تنفيذ واضحة",
    "متابعة القرار وقياس الأثر",
    "حفظ رحلة الشركة وتطورها",
)
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "free"}
STALE_CHECKOUT_MIN_AGE_HOURS = 24
BILLING_CLEANUP_STALE_AFTER_HOURS = 48
BILLING_CLEANUP_LOCK_NAME = "sana.billing.stale-checkout-cleanup"
CLEANUP_COUNTER_KEYS = (
    "scanned",
    "expired",
    "already_completed",
    "already_expired",
    "failed",
)
CLEANUP_RUN_STATUSES = {"completed", "skipped_locked"}
STRIPE_SUBSCRIPTION_EVENT_TYPES = {
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


def _id(prefix):
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def ensure_schema(db):
    acquire_schema_lock(db)
    db.execute("""CREATE TABLE IF NOT EXISTS sana_billing_settings (
        plan_id TEXT PRIMARY KEY,
        plan_name TEXT NOT NULL,
        headline TEXT NOT NULL,
        base_price_minor INTEGER NOT NULL CHECK (base_price_minor >= 0),
        sale_price_minor INTEGER NOT NULL CHECK (sale_price_minor >= 0),
        currency TEXT NOT NULL DEFAULT 'sar',
        sale_label TEXT NOT NULL DEFAULT 'عرض الإطلاق',
        stripe_product_id TEXT,
        stripe_price_id TEXT,
        stripe_price_amount_minor INTEGER,
        updated_by TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute(
        "ALTER TABLE sana_billing_settings ADD COLUMN IF NOT EXISTS stripe_price_id TEXT"
    )
    db.execute(
        """ALTER TABLE sana_billing_settings
           ADD COLUMN IF NOT EXISTS stripe_price_amount_minor INTEGER"""
    )
    db.execute("""CREATE TABLE IF NOT EXISTS sana_billing_coupons (
        coupon_id TEXT PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        discount_type TEXT NOT NULL CHECK (discount_type IN ('percent','amount')),
        discount_value INTEGER NOT NULL CHECK (discount_value > 0),
        max_redemptions INTEGER CHECK (max_redemptions IS NULL OR max_redemptions > 0),
        used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
        expires_at DATE,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS sana_company_subscriptions (
        subscription_id TEXT PRIMARY KEY,
        company_id TEXT UNIQUE NOT NULL REFERENCES companies(company_id),
        status TEXT NOT NULL CHECK (
          status IN ('pending','active','free','paused','past_due','canceled')
        ),
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        stripe_checkout_session_id TEXT UNIQUE,
        coupon_code TEXT,
        amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (amount_minor >= 0),
        started_at TIMESTAMPTZ,
        current_period_end TIMESTAMPTZ,
        last_stripe_event_created BIGINT,
        last_stripe_event_id TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute(
        """ALTER TABLE sana_company_subscriptions
           ADD COLUMN IF NOT EXISTS last_stripe_event_created BIGINT"""
    )
    db.execute(
        """ALTER TABLE sana_company_subscriptions
           ADD COLUMN IF NOT EXISTS last_stripe_event_id TEXT"""
    )
    db.execute(
        """ALTER TABLE sana_company_subscriptions
           ADD COLUMN IF NOT EXISTS stripe_checkout_session_created_at TIMESTAMPTZ"""
    )
    db.execute(
        """UPDATE sana_company_subscriptions
           SET stripe_checkout_session_created_at=updated_at
           WHERE status='pending'
             AND stripe_checkout_session_id IS NOT NULL
             AND stripe_checkout_session_created_at IS NULL"""
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS
           idx_sana_pending_checkout_cleanup
           ON sana_company_subscriptions (stripe_checkout_session_created_at)
           WHERE status='pending' AND stripe_checkout_session_id IS NOT NULL"""
    )
    db.execute("""CREATE TABLE IF NOT EXISTS sana_billing_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        company_id TEXT REFERENCES companies(company_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS sana_billing_cleanup_runs (
        singleton_key TEXT PRIMARY KEY DEFAULT 'latest'
            CHECK (singleton_key = 'latest'),
        run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        status TEXT NOT NULL CHECK (status = 'completed'),
        scanned INTEGER NOT NULL DEFAULT 0 CHECK (scanned >= 0),
        expired INTEGER NOT NULL DEFAULT 0 CHECK (expired >= 0),
        already_completed INTEGER NOT NULL DEFAULT 0 CHECK (already_completed >= 0),
        already_expired INTEGER NOT NULL DEFAULT 0 CHECK (already_expired >= 0),
        failed INTEGER NOT NULL DEFAULT 0 CHECK (failed >= 0)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS sana_billing_cleanup_skips (
        singleton_key TEXT PRIMARY KEY DEFAULT 'latest'
            CHECK (singleton_key = 'latest'),
        run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        status TEXT NOT NULL CHECK (status = 'skipped_locked'),
        scanned INTEGER NOT NULL DEFAULT 0 CHECK (scanned >= 0),
        expired INTEGER NOT NULL DEFAULT 0 CHECK (expired >= 0),
        already_completed INTEGER NOT NULL DEFAULT 0 CHECK (already_completed >= 0),
        already_expired INTEGER NOT NULL DEFAULT 0 CHECK (already_expired >= 0),
        failed INTEGER NOT NULL DEFAULT 0 CHECK (failed >= 0)
    )""")
    db.execute(
        """INSERT INTO sana_billing_settings
           (plan_id,plan_name,headline,base_price_minor,sale_price_minor,currency,sale_label)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (plan_id) DO NOTHING""",
        (
            PLAN_ID,
            "سنع",
            "وضوح أكبر لشركتك\nقرار أوضح\nومتابعة تقيس النتيجة",
            99000,
            49000,
            "sar",
            "عرض الإطلاق",
        ),
    )
    db.commit()


def settings(db):
    row = db.execute(
        "SELECT * FROM sana_billing_settings WHERE plan_id=?",
        (PLAN_ID,),
    ).fetchone()
    return dict(row)


def normalize_coupon_code(value):
    return str(value or "").strip().upper()


def _coupon_row(db, code):
    normalized = normalize_coupon_code(code)
    if not normalized:
        return None
    row = db.execute(
        """SELECT * FROM sana_billing_coupons
           WHERE code=? AND is_active=true
             AND (expires_at IS NULL OR expires_at>=CURRENT_DATE)
             AND (max_redemptions IS NULL OR used_count<max_redemptions)""",
        (normalized,),
    ).fetchone()
    return dict(row) if row else None


def quote(db, code=None):
    offer = settings(db)
    price = int(offer["sale_price_minor"])
    coupon = _coupon_row(db, code)
    if code and not coupon:
        raise ValueError("COUPON_INVALID")
    discount = 0
    if coupon:
        if coupon["discount_type"] == "percent":
            discount = round(price * int(coupon["discount_value"]) / 100)
        else:
            discount = int(coupon["discount_value"])
    discount = min(price, max(0, discount))
    return {
        "price_minor": price,
        "discount_minor": discount,
        "amount_now_minor": price - discount,
        "coupon_code": coupon["code"] if coupon else None,
    }


def subscription_for_company(db, company_id):
    row = db.execute(
        """SELECT * FROM sana_company_subscriptions
           WHERE company_id=?""",
        (company_id,),
    ).fetchone()
    return dict(row) if row else None


def has_company_access(db, company_id):
    """Return True only for an active or granted-free company subscription."""
    subscription = subscription_for_company(db, company_id)
    if not subscription or subscription.get("status") not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    period_end = subscription.get("current_period_end")
    if not period_end:
        return True
    try:
        if getattr(period_end, "tzinfo", None):
            now = datetime.now(period_end.tzinfo)
        else:
            now = datetime.utcnow()
        return period_end >= now
    except TypeError:
        return True


def activate_free(db, company_id, *, coupon_code=None, period_days=30):
    now = datetime.utcnow()
    subscription_id = _id("SUB")
    db.execute(
        """INSERT INTO sana_company_subscriptions
           (subscription_id,company_id,status,coupon_code,amount_minor,
            started_at,current_period_end)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (company_id) DO UPDATE SET
             status='free',coupon_code=EXCLUDED.coupon_code,amount_minor=0,
             started_at=COALESCE(sana_company_subscriptions.started_at,EXCLUDED.started_at),
             current_period_end=EXCLUDED.current_period_end,updated_at=now()""",
        (
            subscription_id, company_id, "free", coupon_code, 0, now,
            now + timedelta(days=period_days),
        ),
    )
    if coupon_code:
        db.execute(
            """UPDATE sana_billing_coupons SET used_count=used_count+1
               WHERE code=? AND is_active=true
                 AND (max_redemptions IS NULL OR used_count<max_redemptions)""",
            (normalize_coupon_code(coupon_code),),
        )
    db.commit()
    return subscription_for_company(db, company_id)


def _connector_settings():
    hostname = (
        os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
        or os.environ.get("CONNECTORS_HOSTNAME")
    )
    identity = os.environ.get("REPL_IDENTITY")
    renewal = os.environ.get("WEB_REPL_RENEWAL")
    auth = f"repl {identity}" if identity else (f"depl {renewal}" if renewal else None)
    if not hostname or not auth:
        raise RuntimeError("STRIPE_CONNECTION_UNAVAILABLE")
    base = hostname if hostname.startswith("http") else f"https://{hostname}"
    response = requests.get(
        f"{base}/api/v2/connection",
        params={"include_secrets": "true", "connector_names": "stripe"},
        headers={"Accept": "application/json", "X_REPLIT_TOKEN": auth},
        timeout=10,
    )
    response.raise_for_status()
    items = (response.json() or {}).get("items") or []
    settings_payload = (items[0].get("settings") if items else None) or {}
    secret_key = settings_payload.get("secret_key") or settings_payload.get("secret")
    if not secret_key:
        raise RuntimeError("STRIPE_CONNECTION_UNAVAILABLE")
    return {**settings_payload, "secret_key": secret_key}


def _stripe_request(method, path, data=None):
    stripe_settings = _connector_settings()
    response = requests.request(
        method,
        f"https://api.stripe.com{path}",
        headers={
            "Authorization": f"Bearer {stripe_settings['secret_key']}",
            "Stripe-Version": "2024-06-20",
        },
        data=data,
        timeout=20,
    )
    payload = response.json()
    if response.status_code >= 400:
        message = ((payload.get("error") or {}).get("message") or "STRIPE_REQUEST_FAILED")
        raise RuntimeError(message)
    return payload


def ensure_stripe_product(db):
    offer = settings(db)
    if offer.get("stripe_product_id"):
        return offer["stripe_product_id"]
    product = _stripe_request("POST", "/v1/products", {
        "name": offer["plan_name"],
        "description": offer["headline"].replace("\n", " · "),
        "metadata[plan_id]": PLAN_ID,
    })
    db.execute(
        """UPDATE sana_billing_settings
           SET stripe_product_id=?,updated_at=now() WHERE plan_id=?""",
        (product["id"], PLAN_ID),
    )
    db.commit()
    return product["id"]


def ensure_stripe_price(db):
    offer = settings(db)
    amount = int(offer["sale_price_minor"])
    if (
        offer.get("stripe_price_id")
        and int(offer.get("stripe_price_amount_minor") or -1) == amount
    ):
        return offer["stripe_price_id"]
    product_id = ensure_stripe_product(db)
    price = _stripe_request("POST", "/v1/prices", {
        "currency": "sar",
        "unit_amount": str(amount),
        "product": product_id,
        "recurring[interval]": "month",
        "metadata[plan_id]": PLAN_ID,
    })
    db.execute(
        """UPDATE sana_billing_settings
           SET stripe_price_id=?,stripe_price_amount_minor=?,updated_at=now()
           WHERE plan_id=?""",
        (price["id"], amount, PLAN_ID),
    )
    db.commit()
    return price["id"]


def create_checkout(db, *, company_id, email, amount_minor, coupon_code, base_url):
    product_id = ensure_stripe_product(db)
    offer = settings(db)
    payload = {
        "mode": "subscription",
        "customer_email": email,
        "success_url": f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/pricing",
        "line_items[0][quantity]": "1",
        "metadata[company_id]": company_id,
        "metadata[coupon_code]": coupon_code or "",
        "subscription_data[metadata][company_id]": company_id,
        "subscription_data[metadata][coupon_code]": coupon_code or "",
    }
    if amount_minor == int(offer["sale_price_minor"]):
        payload["line_items[0][price]"] = ensure_stripe_price(db)
    else:
        payload.update({
            "line_items[0][price_data][currency]": "sar",
            "line_items[0][price_data][unit_amount]": str(amount_minor),
            "line_items[0][price_data][product]": product_id,
            "line_items[0][price_data][recurring][interval]": "month",
        })
    session_payload = _stripe_request("POST", "/v1/checkout/sessions", payload)
    db.execute(
        """INSERT INTO sana_company_subscriptions
           (subscription_id,company_id,status,stripe_checkout_session_id,
            stripe_checkout_session_created_at,coupon_code,amount_minor)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (company_id) DO UPDATE SET
             status='pending',
             stripe_checkout_session_id=EXCLUDED.stripe_checkout_session_id,
             stripe_checkout_session_created_at=now(),
              stripe_customer_id=NULL,
              stripe_subscription_id=NULL,
             coupon_code=EXCLUDED.coupon_code,
             amount_minor=EXCLUDED.amount_minor,
              last_stripe_event_created=NULL,
              last_stripe_event_id=NULL,
             updated_at=now()""",
        (
            _id("SUB"), company_id, "pending", session_payload["id"],
            datetime.utcnow(), coupon_code, amount_minor,
        ),
    )
    db.commit()
    return {"session_id": session_payload["id"], "url": session_payload["url"]}


def retrieve_checkout(session_id):
    return _stripe_request(
        "GET",
        f"/v1/checkout/sessions/{session_id}?expand[]=subscription",
    )


def expire_stale_checkouts(db, *, min_age_hours=STALE_CHECKOUT_MIN_AGE_HOURS,
                           limit=50, company_ids=None):
    """Expire only old, still-pending Sana Checkout sessions.

    ``company_ids`` can restrict a maintenance/test run to an explicit set of
    tenants.  The default remains global for the production worker.

    Each local row is locked while its Stripe state is checked and expired,
    then the transaction is completed before the next row is processed.  A
    completed or paid session is never sent to Stripe's expire endpoint, and a
    local row is marked canceled only after Stripe confirms
    ``status=expired``.  A provider failure rolls back that row immediately,
    leaving it pending and releasing its lock for a later payment attempt.
    """
    try:
        min_age_hours = int(min_age_hours)
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("CHECKOUT_CLEANUP_ARGUMENTS_INVALID")
    if min_age_hours < STALE_CHECKOUT_MIN_AGE_HOURS:
        raise ValueError("CHECKOUT_CLEANUP_AGE_TOO_SHORT")
    if limit < 1 or limit > 100:
        raise ValueError("CHECKOUT_CLEANUP_LIMIT_INVALID")

    cutoff = datetime.utcnow() - timedelta(hours=min_age_hours)
    company_ids = (
        tuple(dict.fromkeys(str(company_id) for company_id in company_ids))
        if company_ids is not None
        else None
    )
    if company_ids is not None and not company_ids:
        return {
            "scanned": 0,
            "expired": 0,
            "already_completed": 0,
            "already_expired": 0,
            "failed": 0,
        }
    company_filter = ""
    if company_ids is not None:
        marks = ",".join("?" for _ in company_ids)
        company_filter = f" AND company_id IN ({marks})"
    candidates = db.execute(
        f"""SELECT subscription_id,company_id,stripe_checkout_session_id
            FROM sana_company_subscriptions
            WHERE status='pending'
              AND stripe_checkout_session_id IS NOT NULL
              AND stripe_checkout_session_created_at IS NOT NULL
              AND stripe_checkout_session_created_at <= ?
              {company_filter}
            ORDER BY stripe_checkout_session_created_at ASC
            LIMIT ?""",
        tuple([cutoff, *company_ids, limit])
        if company_ids is not None
        else (cutoff, limit),
    ).fetchall()
    # The candidate list is only a snapshot.  Re-lock and re-read each row
    # below so a concurrent completion or webhook can win before Stripe is
    # contacted, while avoiding locks across the whole batch.
    db.commit()
    counts = {
        "scanned": 0,
        "expired": 0,
        "already_completed": 0,
        "already_expired": 0,
        "failed": 0,
    }

    for candidate in candidates:
        row = db.execute(
            """SELECT subscription_id,company_id,stripe_checkout_session_id
               FROM sana_company_subscriptions
               WHERE subscription_id=? AND status='pending'
                 AND stripe_checkout_session_id IS NOT NULL
                 AND stripe_checkout_session_created_at IS NOT NULL
                 AND stripe_checkout_session_created_at <= ?
               FOR UPDATE SKIP LOCKED""",
            (candidate["subscription_id"], cutoff),
        ).fetchone()
        if not row:
            db.rollback()
            continue

        counts["scanned"] += 1
        session_id = row["stripe_checkout_session_id"]
        try:
            checkout = retrieve_checkout(session_id)
            payment_status = checkout.get("payment_status")
            remote_status = checkout.get("status")
            if payment_status == "paid" or remote_status == "complete":
                counts["already_completed"] += 1
            else:
                if remote_status == "expired":
                    expired = True
                    counts["already_expired"] += 1
                elif remote_status == "open" and payment_status != "paid":
                    expired_payload = _stripe_request(
                        "POST", f"/v1/checkout/sessions/{session_id}/expire"
                    )
                    expired = (
                        expired_payload.get("status") == "expired"
                        and expired_payload.get("payment_status") != "paid"
                    )
                else:
                    expired = False

                if not expired:
                    counts["failed"] += 1
                else:
                    updated = db.execute(
                        """UPDATE sana_company_subscriptions
                           SET status='canceled',updated_at=now()
                           WHERE subscription_id=? AND status='pending'
                             AND stripe_checkout_session_id=?""",
                        (row["subscription_id"], session_id),
                    )
                    if updated.rowcount == 1 and remote_status == "open":
                        counts["expired"] += 1
        except Exception:
            # Keep the row pending for a later retry; do not expose provider
            # details or identifiers in the cleanup result.
            db.rollback()
            counts["failed"] += 1
        else:
            # Commit even for completed/paid or already-expired remote
            # sessions, since the row lock must not survive this iteration.
            db.commit()

    return counts


def try_acquire_billing_cleanup_lock(db):
    """Claim the cleanup lease for this database connection, if it is free.

    This is intentionally a session-level PostgreSQL advisory lock.  The
    dedicated worker keeps one connection for the whole run, so a second
    cron invocation can safely exit without touching any checkout row.
    """
    row = db.execute(
        "SELECT pg_try_advisory_lock(hashtext(?)) AS acquired",
        (BILLING_CLEANUP_LOCK_NAME,),
    ).fetchone()
    return bool(row["acquired"] if isinstance(row, dict) else row[0])


def release_billing_cleanup_lock(db):
    """Release a previously claimed cleanup lease."""
    row = db.execute(
        "SELECT pg_advisory_unlock(hashtext(?)) AS released",
        (BILLING_CLEANUP_LOCK_NAME,),
    ).fetchone()
    db.commit()
    return bool(row["released"] if isinstance(row, dict) else row[0])


def _sanitized_cleanup_counts(counts=None):
    """Keep cleanup output to stable counters; never return provider IDs."""
    counts = counts or {}
    return {
        key: int(counts.get(key, 0) or 0)
        for key in CLEANUP_COUNTER_KEYS
    }

def record_billing_cleanup_run(db, result):
    """Persist one safe worker result without provider IDs.

    Completed cleanup and advisory-lock skips use separate singleton rows. A
    no-op contender therefore cannot replace the last completed cleanup, even
    when the worker holding the lock later fails.
    """
    status = str((result or {}).get("status") or "")
    if status not in CLEANUP_RUN_STATUSES:
        raise ValueError("BILLING_CLEANUP_RESULT_INVALID")
    counts = _sanitized_cleanup_counts(result)
    table_name = (
        "sana_billing_cleanup_runs"
        if status == "completed"
        else "sana_billing_cleanup_skips"
    )
    db.execute(
        f"""INSERT INTO {table_name}
           (singleton_key,status,scanned,expired,already_completed,
            already_expired,failed)
           VALUES ('latest',?,?,?,?,?,?)
           ON CONFLICT (singleton_key) DO UPDATE SET
             run_at=now(),
             status=EXCLUDED.status,
             scanned=EXCLUDED.scanned,
             expired=EXCLUDED.expired,
             already_completed=EXCLUDED.already_completed,
             already_expired=EXCLUDED.already_expired,
             failed=EXCLUDED.failed""",
        (
            status,
            counts["scanned"],
            counts["expired"],
            counts["already_completed"],
            counts["already_expired"],
            counts["failed"],
        ),
    )
    db.commit()
    return {"status": status, **counts}
def run_billing_cleanup_once(
    db, *, min_age_hours=STALE_CHECKOUT_MIN_AGE_HOURS, limit=50
):
    """Run one guarded cleanup and return a redacted operational result.

    The lock is held only by the caller's database connection and is released
    before returning.  ``skipped_locked`` is a successful no-op: another
    production worker already owns this scheduled run.
    """
    empty = _sanitized_cleanup_counts()
    if not try_acquire_billing_cleanup_lock(db):
        result = {"status": "skipped_locked", **empty}
        record_billing_cleanup_run(db, result)
        return result

    try:
        counts = expire_stale_checkouts(
            db, min_age_hours=min_age_hours, limit=limit
        )
        result = {"status": "completed", **_sanitized_cleanup_counts(counts)}
        record_billing_cleanup_run(db, result)
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        release_billing_cleanup_lock(db)


def complete_checkout(db, checkout):
    metadata = checkout.get("metadata") or {}
    company_id = metadata.get("company_id")
    if not company_id:
        raise ValueError("CHECKOUT_COMPANY_MISSING")
    if checkout.get("payment_status") != "paid":
        raise ValueError("PAYMENT_NOT_COMPLETE")
    event_id = f"checkout:{checkout['id']}"
    claimed = db.execute(
        """INSERT INTO sana_billing_events
           (event_id,event_type,company_id) VALUES (?,?,?)
           ON CONFLICT (event_id) DO NOTHING
           RETURNING event_id""",
        (event_id, "checkout.session.completed", company_id),
    ).fetchone()
    if not claimed:
        # The browser return and Stripe's webhook can arrive together. The
        # unique event claim makes later retries successful no-ops.
        db.commit()
        return subscription_for_company(db, company_id)
    current = db.execute(
        """SELECT status FROM sana_company_subscriptions
           WHERE company_id=? AND stripe_checkout_session_id=?
           FOR UPDATE""",
        (company_id, checkout["id"]),
    ).fetchone()
    if not current:
        raise ValueError("CHECKOUT_SUBSCRIPTION_NOT_FOUND")
    subscription = checkout.get("subscription")
    stripe_subscription_id = (
        subscription.get("id") if isinstance(subscription, dict) else subscription
    )
    period_end = None
    if isinstance(subscription, dict) and subscription.get("current_period_end"):
        period_end = datetime.utcfromtimestamp(subscription["current_period_end"])
    updated = db.execute(
        """UPDATE sana_company_subscriptions
           SET status=CASE WHEN status='canceled' THEN status ELSE 'active' END,
               stripe_customer_id=?,stripe_subscription_id=?,
               started_at=COALESCE(started_at,now()),
               current_period_end=COALESCE(?,current_period_end),updated_at=now()
           WHERE company_id=? AND stripe_checkout_session_id=?""",
        (
            checkout.get("customer"), stripe_subscription_id, period_end,
            company_id, checkout["id"],
        ),
    )
    if updated.rowcount != 1:
        raise ValueError("CHECKOUT_SESSION_NOT_FOUND")
    coupon_code = normalize_coupon_code(metadata.get("coupon_code"))
    if coupon_code:
        db.execute(
            """UPDATE sana_billing_coupons SET used_count=used_count+1
               WHERE code=? AND is_active=true
                 AND (max_redemptions IS NULL OR used_count<max_redemptions)""",
            (coupon_code,),
        )
    db.commit()
    return subscription_for_company(db, company_id)


def _stripe_id(value):
    """Return a Stripe identifier whether Stripe sent a string or an object."""
    if isinstance(value, dict):
        return value.get("id")
    return value


def _stripe_period_end(value):
    """Convert Stripe's Unix timestamp into the database's UTC datetime."""
    if value in (None, ""):
        return None
    try:
        return datetime.utcfromtimestamp(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _subscription_period_end(stripe_object, event_type):
    period_end = stripe_object.get("current_period_end")
    if period_end:
        return _stripe_period_end(period_end)

    # Invoices expose the paid subscription period on their line items.  Use
    # the latest line end so a multi-line invoice cannot shorten the period.
    if event_type.startswith("invoice."):
        ends = []
        for line in (stripe_object.get("lines") or {}).get("data") or []:
            line_end = _stripe_period_end((line.get("period") or {}).get("end"))
            if line_end:
                ends.append(line_end)
        if ends:
            return max(ends)
        return _stripe_period_end(stripe_object.get("period_end"))
    return None


def _stripe_subscription_row(db, stripe_object, event_type):
    """Resolve one existing Sana subscription without trusting tenant input.

    Stripe events are mapped using IDs already stored during Checkout.  The
    company metadata fallback is only accepted when it points at an existing
    subscription and does not contradict either Stripe identifier.
    """
    stripe_subscription_id = _stripe_id(
        stripe_object.get("subscription")
        if event_type.startswith("invoice.")
        else stripe_object.get("id")
    )
    stripe_customer_id = _stripe_id(stripe_object.get("customer"))
    exact_subscription = None
    if stripe_subscription_id:
        exact_subscription = db.execute(
            """SELECT * FROM sana_company_subscriptions
               WHERE stripe_subscription_id=? FOR UPDATE""",
            (stripe_subscription_id,),
        ).fetchone()
        if exact_subscription:
            if (
                stripe_customer_id
                and exact_subscription["stripe_customer_id"]
                and exact_subscription["stripe_customer_id"] != stripe_customer_id
            ):
                return None, stripe_subscription_id, stripe_customer_id
            return exact_subscription, stripe_subscription_id, stripe_customer_id

    customer_subscription = None
    if stripe_customer_id:
        customer_matches = db.execute(
            """SELECT * FROM sana_company_subscriptions
               WHERE stripe_customer_id=? FOR UPDATE""",
            (stripe_customer_id,),
        ).fetchall()
        customer_subscription = customer_matches[0] if len(customer_matches) == 1 else None
        if customer_subscription and (
            not stripe_subscription_id
            or customer_subscription["stripe_subscription_id"] == stripe_subscription_id
        ):
            return customer_subscription, stripe_subscription_id, stripe_customer_id

    metadata = stripe_object.get("metadata") or (
        (stripe_object.get("subscription_details") or {}).get("metadata") or {}
    )
    metadata_company_id = metadata.get("company_id")
    row = None
    if metadata_company_id:
        pending = db.execute(
            """SELECT * FROM sana_company_subscriptions
               WHERE company_id=? AND status='pending'
                 AND stripe_subscription_id IS NULL
               FOR UPDATE""",
            (metadata_company_id,),
        ).fetchone()
        if pending and (
            not stripe_customer_id
            or not pending["stripe_customer_id"]
            or pending["stripe_customer_id"] == stripe_customer_id
        ):
            row = pending

    # A customer match must never replace a different stored subscription.
    if (
        not row
        and customer_subscription
        and stripe_subscription_id
        and customer_subscription["stripe_subscription_id"] != stripe_subscription_id
    ):
        return None, stripe_subscription_id, stripe_customer_id
    if row and (
        (
            stripe_subscription_id
            and row["stripe_subscription_id"]
            and row["stripe_subscription_id"] != stripe_subscription_id
        )
        or (
            stripe_customer_id
            and row["stripe_customer_id"]
            and row["stripe_customer_id"] != stripe_customer_id
        )
    ):
        row = None
    return row, stripe_subscription_id, stripe_customer_id


def _stripe_subscription_status(event_type, stripe_object):
    if event_type == "invoice.paid":
        return "active"
    if event_type == "invoice.payment_failed":
        return "past_due"
    if event_type == "customer.subscription.deleted":
        return "canceled"

    status = str(stripe_object.get("status") or "").lower()
    return {
        "active": "active",
        "trialing": "active",
        "past_due": "past_due",
        "unpaid": "past_due",
        "canceled": "canceled",
        "incomplete_expired": "canceled",
        "incomplete": "pending",
        "paused": "paused",
    }.get(status)


def process_stripe_event(db, event):
    """Apply one supported Stripe event exactly once.

    The billing event ledger is also the webhook audit trail.  It is written
    in the same transaction as the subscription update, so a retry after a
    failed update can safely apply again while a delivered duplicate is a
    no-op.
    """
    event_id = str(event.get("id") or "").strip()
    event_type = str(event.get("type") or "").strip()
    if not event_id:
        raise ValueError("STRIPE_EVENT_ID_MISSING")
    if event_type not in STRIPE_SUBSCRIPTION_EVENT_TYPES:
        return {"status": "ignored", "event_id": event_id, "event_type": event_type}

    stripe_object = ((event.get("data") or {}).get("object") or {})
    if not isinstance(stripe_object, dict):
        raise ValueError("STRIPE_EVENT_OBJECT_INVALID")
    subscription, stripe_subscription_id, stripe_customer_id = _stripe_subscription_row(
        db, stripe_object, event_type
    )
    if not subscription:
        raise ValueError("STRIPE_SUBSCRIPTION_NOT_FOUND")
    company_id = subscription["company_id"]

    inserted = db.execute(
        """INSERT INTO sana_billing_events
           (event_id,event_type,company_id)
           VALUES (?,?,?)
           ON CONFLICT (event_id) DO NOTHING""",
        (event_id, event_type, company_id),
    )
    if inserted.rowcount == 0:
        db.commit()
        return {
            "status": "duplicate",
            "event_id": event_id,
            "event_type": event_type,
            "company_id": company_id,
        }

    status = _stripe_subscription_status(event_type, stripe_object)
    previous_status = subscription["status"]
    period_end = _subscription_period_end(stripe_object, event_type)
    try:
        event_created = int(event.get("created") or 0)
    except (TypeError, ValueError):
        raise ValueError("STRIPE_EVENT_CREATED_INVALID")
    last_event_created = int(subscription["last_stripe_event_created"] or 0)
    stale = event_created and last_event_created and event_created < last_event_created
    terminal = subscription["status"] == "canceled" and status != "canceled"
    status_priority = {
        "active": 0,
        "pending": 0,
        "free": 0,
        "paused": 1,
        "past_due": 2,
        "canceled": 3,
    }
    lower_priority_at_same_time = (
        event_created
        and event_created == last_event_created
        and status_priority.get(status, 0)
        < status_priority.get(subscription["status"], 0)
    )
    if stale or terminal or lower_priority_at_same_time:
        db.commit()
        return {
            "status": "stale",
            "event_id": event_id,
            "event_type": event_type,
            "company_id": company_id,
        }

    db.execute(
        """UPDATE sana_company_subscriptions
           SET status=COALESCE(?,status),
               stripe_customer_id=COALESCE(?,stripe_customer_id),
               stripe_subscription_id=COALESCE(?,stripe_subscription_id),
               current_period_end=COALESCE(?,current_period_end),
               last_stripe_event_created=CASE
                 WHEN ? > 0 THEN ? ELSE last_stripe_event_created END,
               last_stripe_event_id=?,
               updated_at=now()
           WHERE company_id=?""",
        (
            status,
            stripe_customer_id,
            stripe_subscription_id,
            period_end,
            event_created,
            event_created,
            event_id,
            company_id,
        ),
    )
    db.commit()
    result = {
        "status": "applied",
        "event_id": event_id,
        "event_type": event_type,
        "company_id": company_id,
        "subscription": subscription_for_company(db, company_id),
    }
    if (
        status in {"past_due", "canceled"}
        and previous_status != status
    ):
        result["notification"] = {
            "status": status,
            "event_id": event_id,
        }
    return result


def verify_webhook(raw_body, signature_header):
    stripe_settings = _connector_settings()
    secret = stripe_settings.get("webhook_secret")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_NOT_CONFIGURED")
    parts = {}
    for item in str(signature_header or "").split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parts.setdefault(key, []).append(value)
    timestamp = int((parts.get("t") or ["0"])[0])
    if abs(int(time.time()) - timestamp) > 300:
        raise ValueError("STRIPE_SIGNATURE_EXPIRED")
    signed = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in parts.get("v1", [])):
        raise ValueError("STRIPE_SIGNATURE_INVALID")
    return json.loads(raw_body)


def public_offer(db, company_id=None):
    offer = settings(db)
    subscription = subscription_for_company(db, company_id) if company_id else None
    return {
        "plan_id": offer["plan_id"],
        "name": offer["plan_name"],
        "headline": offer["headline"],
        "features": list(DEFAULT_FEATURES),
        "base_price_minor": int(offer["base_price_minor"]),
        "sale_price_minor": int(offer["sale_price_minor"]),
        "currency": offer["currency"],
        "sale_label": offer["sale_label"],
        "subscription": subscription,
    }

def latest_billing_cleanup_run(db):
    """Return the last completed cleanup; lock skips cannot replace it."""
    return _latest_billing_cleanup_result(db, "sana_billing_cleanup_runs")

def latest_billing_cleanup_skip(db):
    """Return the last lock skip separately from the completed cleanup."""
    return _latest_billing_cleanup_result(db, "sana_billing_cleanup_skips")


def billing_cleanup_health(db, *, now=None):
    """Return a redacted freshness state for the scheduled cleanup worker.

    Both a completed run and a lock skip prove that the daily schedule was
    reached.  Keeping this as derived state avoids creating a notification
    every time an administrator opens the dashboard; the warning disappears
    automatically as soon as either table receives a newer reliable result.
    """
    row = db.execute(
        """SELECT run_at,status
           FROM (
             SELECT run_at,status
             FROM sana_billing_cleanup_runs
             WHERE singleton_key='latest'
             UNION ALL
             SELECT run_at,status
             FROM sana_billing_cleanup_skips
             WHERE singleton_key='latest'
           ) cleanup_results
           ORDER BY run_at DESC
           LIMIT 1"""
    ).fetchone()
    last_run_at = row["run_at"] if row else None
    stale = True
    if last_run_at is not None:
        parsed_run_at = _parse_cleanup_timestamp(last_run_at)
        current_time = now or datetime.utcnow()
        if parsed_run_at is not None:
            if parsed_run_at.tzinfo and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=parsed_run_at.tzinfo)
            elif parsed_run_at.tzinfo is None and current_time.tzinfo:
                parsed_run_at = parsed_run_at.replace(
                    tzinfo=current_time.tzinfo
                )
            stale = (
                current_time - parsed_run_at
                > timedelta(hours=BILLING_CLEANUP_STALE_AFTER_HOURS)
            )
    return {
        "status": "needs_attention" if stale else "healthy",
        "is_stale": stale,
        "last_reliable_run_at": last_run_at,
        "last_reliable_status": row["status"] if row else None,
        "stale_after_hours": BILLING_CLEANUP_STALE_AFTER_HOURS,
    }


def _parse_cleanup_timestamp(value):
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _latest_billing_cleanup_result(db, table_name):
    """Read one of the two fixed, provider-ID-free cleanup result tables."""
    if table_name not in {
        "sana_billing_cleanup_runs",
        "sana_billing_cleanup_skips",
    }:
        raise ValueError("BILLING_CLEANUP_RESULT_TABLE_INVALID")
    row = db.execute(
        f"""SELECT run_at,status,scanned,expired,already_completed,
                  already_expired,failed
           FROM {table_name}
           WHERE singleton_key='latest'"""
    ).fetchone()
    if not row:
        return None
    return {
        "run_at": row["run_at"],
        "status": row["status"],
        **_sanitized_cleanup_counts(row),
    }
