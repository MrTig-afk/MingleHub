import asyncio
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_payment
from api.services import stripe_service

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

_SUCCESS_URL = "https://first-move-one.vercel.app/?payment=success"
_CANCEL_URL = "https://first-move-one.vercel.app/?payment=cancelled"

router = APIRouter(prefix="/api")


@router.post("/create-checkout-session", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def create_checkout_session(request: Request):
    if not _PRICE_ID or _PRICE_ID == "price_REPLACE_ME":
        raise HTTPException(status_code=503, detail="Payments not configured yet")

    session = await asyncio.to_thread(
        lambda: stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": _PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url=_SUCCESS_URL,
            cancel_url=_CANCEL_URL,
        )
    )
    return JSONResponse({"url": session.url})


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not _WEBHOOK_SECRET or _WEBHOOK_SECRET == "whsec_REPLACE_ME":
        raise HTTPException(status_code=503, detail="Webhook not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, _WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        data = event["data"]["object"]
        email = data.get("customer_details", {}).get("email", "unknown")
        amount = data.get("amount_total", 0) / 100
        currency = data.get("currency", "usd").upper()
        await notify_payment(
            "💰 New Premium subscriber!",
            f"Email: {email}\nAmount: {currency} {amount:.2f}\nSession: {data['id']}",
        )

    return JSONResponse({"status": "ok"})


@router.post("/stripe/usage-webhook")
async def usage_webhook(request: Request):
    """Usage-billing webhook: Stripe invoice.paid / payment_failed -> move the
    invoice's status. Signature-verified (Stripe HMAC scheme). Public, but a bad
    signature is rejected with 400."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe_service.verify_webhook(
            payload, sig_header, stripe_service.STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    pool = await get_pool()
    async with pool.acquire() as conn:
        applied = await stripe_service.apply_invoice_event(conn, event)
        if applied == "paid":
            from api.services import venue_lifecycle_service
            stripe_inv_id = (event.get("data") or {}).get("object", {}).get("id")
            if stripe_inv_id:
                venue_id = await conn.fetchval(
                    "SELECT venue_id FROM invoices WHERE stripe_invoice_id = $1",
                    stripe_inv_id,
                )
                if venue_id:
                    await venue_lifecycle_service.auto_reactivate_on_payment(conn, str(venue_id))
    return JSONResponse({"status": "ok", "applied": applied})
