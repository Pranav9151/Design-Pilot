"""
Billing API — Stripe integration.

Endpoints:
  POST /api/v1/billing/checkout          Create a Stripe Checkout session
  POST /api/v1/billing/portal            Create a Billing Portal session
  POST /api/v1/billing/webhook           Stripe webhook receiver
  GET  /api/v1/billing/status            Current user plan + limits

Plan → permission mapping:
  free   — 3 designs/month, no advanced AI endpoints
  pro    — 50 designs/month, all AI endpoints unlocked
  team   — 200 designs/month, collaboration features

The plan field lives on the users table and is updated by the webhook
handler when Stripe fires subscription events.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.iam.deps import CurrentUser, get_current_user
from app.models.user import User

router = APIRouter()
logger = structlog.get_logger(__name__)

Plan = Literal["free", "pro", "team"]

# Stripe Price IDs — set these in .env
PLAN_TO_PRICE = {
    "pro": "STRIPE_PRICE_PRO",
    "team": "STRIPE_PRICE_TEAM",
}

PLAN_LIMITS: dict[str, dict] = {
    "free": {"designs_per_month": 3, "ai_endpoints": False},
    "pro": {"designs_per_month": 50, "ai_endpoints": True},
    "team": {"designs_per_month": 200, "ai_endpoints": True},
}


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "team"]
    success_url: str = Field(..., min_length=10)
    cancel_url: str = Field(..., min_length=10)


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class BillingStatus(BaseModel):
    plan: str
    designs_per_month: int
    ai_endpoints_enabled: bool
    stripe_customer_id: str | None


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/status", response_model=BillingStatus)
async def billing_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BillingStatus:
    """Return the current user's plan and usage limits."""
    row = (await db.execute(
        select(User).where(User.id == current_user.id)
    )).scalar_one_or_none()

    plan = (row.plan if row else "free") or "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    stripe_cid = getattr(row, "stripe_customer_id", None) if row else None

    return BillingStatus(
        plan=plan,
        designs_per_month=limits["designs_per_month"],
        ai_endpoints_enabled=limits["ai_endpoints"],
        stripe_customer_id=stripe_cid,
    )


@router.post("/checkout", response_model=CheckoutResponse, status_code=status.HTTP_200_OK)
async def create_checkout_session(
    payload: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """
    Create a Stripe Checkout session for upgrading to Pro or Team.

    Requires STRIPE_SECRET_KEY in .env.
    Returns a redirect URL — the frontend redirects the user to it.
    """
    settings = get_settings()
    stripe_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured — set STRIPE_SECRET_KEY in .env",
        )

    try:
        import stripe
        stripe.api_key = stripe_key

        price_env_key = PLAN_TO_PRICE[payload.plan]
        price_id = getattr(settings, price_env_key, None)
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{price_env_key} not set in .env",
            )

        row = (await db.execute(
            select(User).where(User.id == current_user.id)
        )).scalar_one_or_none()

        customer_id = getattr(row, "stripe_customer_id", None) if row else None

        session_kwargs: dict = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": payload.success_url + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": payload.cancel_url,
            "metadata": {"user_id": str(current_user.id), "plan": payload.plan},
            "subscription_data": {
                "metadata": {"user_id": str(current_user.id), "plan": payload.plan}
            },
        }
        if customer_id:
            session_kwargs["customer"] = customer_id
        else:
            session_kwargs["customer_email"] = getattr(current_user, "email", None)

        session = stripe.checkout.Session.create(**session_kwargs)

        logger.info("stripe_checkout_created", user_id=str(current_user.id), plan=payload.plan)
        return CheckoutResponse(checkout_url=session.url)

    except Exception as exc:
        logger.error("stripe_checkout_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Stripe error: {exc}",
        ) from exc


@router.post("/portal", response_model=PortalResponse)
async def create_billing_portal(
    return_url: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    """Open the Stripe Customer Portal for plan management / cancellation."""
    settings = get_settings()
    stripe_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Billing not configured")

    row = (await db.execute(
        select(User).where(User.id == current_user.id)
    )).scalar_one_or_none()

    customer_id = getattr(row, "stripe_customer_id", None) if row else None
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer found for this account. Subscribe first.",
        )

    try:
        import stripe
        stripe.api_key = stripe_key
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return PortalResponse(portal_url=session.url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
) -> dict:
    """
    Receive and process Stripe webhook events.

    Handles:
      checkout.session.completed       — link customer, set plan
      customer.subscription.updated    — plan change mid-cycle
      customer.subscription.deleted    — downgrade to free
    """
    settings = get_settings()
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)

    body = await request.body()

    if webhook_secret and stripe_signature:
        try:
            import stripe
            stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
            event = stripe.Webhook.construct_event(body, stripe_signature, webhook_secret)
        except Exception as exc:
            logger.warning("stripe_webhook_invalid_signature", error=str(exc))
            raise HTTPException(status_code=400, detail="Invalid signature") from exc
    else:
        # No webhook secret configured — accept all (dev only)
        import json
        try:
            event = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        logger.warning("stripe_webhook_no_signature_check", event_type=event.get("type"))

    await _handle_stripe_event(event, db)
    return {"received": True}


async def _handle_stripe_event(event: dict, db: AsyncSession) -> None:
    """Dispatch Stripe events to the appropriate handler."""
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user_id = data.get("metadata", {}).get("user_id")
        plan = data.get("metadata", {}).get("plan", "pro")
        customer_id = data.get("customer")
        if user_id:
            await _set_user_plan(db, user_id, plan, customer_id)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        # Map Stripe price → plan
        items = data.get("items", {}).get("data", [])
        plan = _plan_from_items(items)
        customer_id = data.get("customer")
        user_id = data.get("metadata", {}).get("user_id")
        if user_id and plan:
            await _set_user_plan(db, user_id, plan, customer_id)

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id:
            await db.execute(
                update(User)
                .where(User.stripe_customer_id == customer_id)  # type: ignore[attr-defined]
                .values(plan="free")
            )
            await db.commit()
            logger.info("stripe_subscription_deleted_downgraded_to_free", customer_id=customer_id)

    else:
        logger.debug("stripe_webhook_unhandled", event_type=event_type)


async def _set_user_plan(
    db: AsyncSession,
    user_id: str,
    plan: str,
    customer_id: str | None,
) -> None:
    from uuid import UUID
    try:
        uid = UUID(user_id)
    except ValueError:
        logger.error("stripe_webhook_invalid_user_id", user_id=user_id)
        return

    values: dict = {"plan": plan}
    if customer_id:
        values["stripe_customer_id"] = customer_id

    await db.execute(
        update(User).where(User.id == uid).values(**values)
    )
    await db.commit()
    logger.info("stripe_user_plan_updated", user_id=user_id, plan=plan)


def _plan_from_items(items: list) -> str | None:
    """Map Stripe line items to a plan slug."""
    for item in items:
        price_id = item.get("price", {}).get("id", "")
        settings = get_settings()
        if price_id == getattr(settings, "STRIPE_PRICE_PRO", None):
            return "pro"
        if price_id == getattr(settings, "STRIPE_PRICE_TEAM", None):
            return "team"
    return None
