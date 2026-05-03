import os
import math
import logging
import stripe
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncpg

from database import get_pool, close_pool
from auth import (
    hash_password, verify_password,
    set_session, clear_session,
    get_current_user, require_user,
    make_delivery_token, decode_delivery_token,
)
import s3_utils
import evidentix_client
import email_utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PK             = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BASE_URL              = os.environ.get("BASE_URL", "http://localhost:8000")
LINK_EXPIRY_HOURS     = int(os.environ.get("DELIVERY_LINK_EXPIRY_HOURS", "72"))

PRICE_BASIC     = int(os.environ.get("PRICE_BASIC_CENTS",     "999"))
PRICE_CERTIFIED = int(os.environ.get("PRICE_CERTIFIED_CENTS", "1999"))
PRICE_CUSTODY   = int(os.environ.get("PRICE_CUSTODY_CENTS",   "4999"))

TIER_PRICES = {"basic": PRICE_BASIC, "certified": PRICE_CERTIFIED, "custody": PRICE_CUSTODY}
TIER_LABELS = {"basic": "Basic Delivery", "certified": "Certified Delivery", "custody": "Custody Package"}

app = FastAPI(title="LegalDrop")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
from jinja2 import FileSystemLoader, Environment as JinjaEnv
_jinja_env = JinjaEnv(loader=FileSystemLoader(["app", "app/templates"]))
templates = Jinja2Templates(env=_jinja_env)

@app.on_event("startup")
async def startup():
    await get_pool()
    logger.info("LegalDrop started")

@app.on_event("shutdown")
async def shutdown():
    await close_pool()


async def ctx(request: Request, **kw) -> dict:
    user = await get_current_user(request)
    return {"request": request, "user": user, "stripe_pk": STRIPE_PK, "base_url": BASE_URL, **kw}


def fmt_dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"

def fmt_dt(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%b %d, %Y %H:%M UTC")

templates.env.filters["dollars"] = fmt_dollars
templates.env.filters["dt"]      = fmt_dt


def client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host or "unknown")


async def log_event(conn, delivery_id: str, event_type: str,
                    ip: str = None, notes: str = None, user_agent: str = None):
    await conn.execute(
        """INSERT INTO delivery_events (delivery_id, event_type, ip_address, notes, user_agent)
           VALUES ($1,$2,$3,$4,$5)""",
        delivery_id, event_type, ip, notes, user_agent,
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("home.html", await ctx(request))


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/dashboard"):
    if await get_current_user(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("auth/login.html", await ctx(request, next=next))


@app.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email=$1", email.lower().strip())
    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            "auth/login.html",
            await ctx(request, error="Invalid email or password.", next=next),
        )
    resp = RedirectResponse(next, status_code=302)
    set_session(resp, str(row["id"]))
    return resp


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", await ctx(request))


@app.post("/register")
async def register_post(
    request: Request,
    full_name:  str = Form(...),
    email:      str = Form(...),
    password:   str = Form(...),
    firm_name:  str = Form(""),
    bar_number: str = Form(""),
    state:      str = Form(""),
):
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/register.html",
            await ctx(request, error="Password must be at least 8 characters."),
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await conn.fetchrow("SELECT id FROM users WHERE email=$1", email.lower().strip()):
            return templates.TemplateResponse(
                "auth/register.html",
                await ctx(request, error="An account with that email already exists."),
            )
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, full_name, firm_name, bar_number, state)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            email.lower().strip(), hash_password(password),
            full_name.strip(), firm_name.strip() or None,
            bar_number.strip() or None, state.strip() or None,
        )
    resp = RedirectResponse("/dashboard", status_code=302)
    set_session(resp, str(row["id"]))
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    clear_session(resp)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await require_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deliveries = await conn.fetch(
            """SELECT d.* FROM deliveries d
               WHERE d.sender_id = $1
               ORDER BY d.created_at DESC LIMIT 50""",
            str(user["id"]),
        )
        stats = await conn.fetchrow(
            """SELECT
               COUNT(*) FILTER (WHERE status='sent')         AS pending,
               COUNT(*) FILTER (WHERE status='opened')       AS opened,
               COUNT(*) FILTER (WHERE status='acknowledged') AS acknowledged,
               COUNT(*)                                      AS total
               FROM deliveries WHERE sender_id=$1""",
            str(user["id"]),
        )
    return templates.TemplateResponse(
        "dashboard.html",
        await ctx(request, deliveries=[dict(d) for d in deliveries], stats=dict(stats)),
    )


@app.get("/deliveries/new", response_class=HTMLResponse)
async def new_delivery_page(request: Request):
    await require_user(request)
    return templates.TemplateResponse(
        "deliveries/new.html",
        await ctx(request, tier_prices=TIER_PRICES, tier_labels=TIER_LABELS,
                  link_expiry_hours=LINK_EXPIRY_HOURS),
    )


@app.post("/deliveries/new")
async def new_delivery_submit(
    request:         Request,
    file:            UploadFile = File(...),
    recipient_email: str  = Form(...),
    recipient_name:  str  = Form(""),
    subject:         str  = Form(...),
    message:         str  = Form(""),
    matter_ref:      str  = Form(""),
    tier:            str  = Form("certified"),
    require_account: bool = Form(False),
    allow_download:  bool = Form(True),
    expires_hours:   int  = Form(72),
):
    user = await require_user(request)
    if tier not in TIER_PRICES:
        raise HTTPException(400, "Invalid tier.")

    import uuid
    delivery_id  = str(uuid.uuid4())
    access_token = make_delivery_token(delivery_id)
    expires_at   = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

    doc = await s3_utils.store_document(file, delivery_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO deliveries
               (id, sender_id, recipient_email, recipient_name, require_account,
                s3_key, filename, file_size_bytes, content_type,
                subject, message, matter_ref,
                access_token, expires_at, allow_download,
                tier, price_cents, sha256)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
            delivery_id, str(user["id"]),
            recipient_email.lower().strip(),
            recipient_name.strip() or None,
            require_account,
            doc["s3_key"], doc["filename"],
            doc["file_size_bytes"], doc["content_type"],
            subject.strip(), message.strip() or None,
            matter_ref.strip() or None,
            access_token, expires_at, allow_download,
            tier, TIER_PRICES[tier], doc["local_sha256"],
        )
        await log_event(conn, delivery_id, "SENT",
                        ip=client_ip(request),
                        notes=f"Delivery created by {user['email']}")

    cert_id = cert_url = None
    try:
        presigned = s3_utils.presigned_url(doc["s3_key"], expiry=900)
        cert = await evidentix_client.certify(
            s3_presigned_url=presigned,
            filename=doc["filename"],
            delivery_id=delivery_id,
            sender_id=str(user["id"]),
            recipient_email=recipient_email,
            matter_ref=matter_ref or None,
        )
        cert_id  = cert.get("certificate_id")
        cert_url = cert.get("cert_url")
        sha256   = cert.get("sha256", doc["local_sha256"])
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE deliveries SET sha256=$1, certificate_id=$2,
                   cert_url=$3, evidentix_verified=TRUE WHERE id=$4""",
                sha256, cert_id, cert_url, delivery_id,
            )
            await log_event(conn, delivery_id, "CERT_ISSUED",
                            notes=f"Evidentix cert: {cert_id}")
    except evidentix_client.EvidentixError as e:
        logger.error("Evidentix cert failed: %s", e)

    if tier == "custody" and cert_id:
        try:
            cr = await evidentix_client.custody_record(
                delivery_id=delivery_id,
                certificate_id=cert_id,
                sender_name=f"{user['full_name']}{' — ' + user['firm_name'] if user.get('firm_name') else ''}",
                recipient_email=recipient_email,
                filename=doc["filename"],
                matter_ref=matter_ref or None,
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE deliveries SET custody_record_id=$1, custody_record_url=$2 WHERE id=$3",
                    cr.get("record_id"), cr.get("record_url"), delivery_id,
                )
        except evidentix_client.EvidentixError as e:
            logger.error("Evidentix custody record failed: %s", e)

    access_url   = f"{BASE_URL}/r/{access_token}"
    register_url = f"{BASE_URL}/recipient/register?token={access_token}"

    if require_account:
        email_utils.recipient_account_invite(
            to_email=recipient_email,
            recipient_name=recipient_name or recipient_email,
            sender_name=user["full_name"],
            firm_name=user.get("firm_name") or "",
            subject=subject,
            register_url=register_url,
            filename=doc["filename"],
        )
    else:
        email_utils.recipient_delivery_link(
            to_email=recipient_email,
            recipient_name=recipient_name or recipient_email,
            sender_name=user["full_name"],
            firm_name=user.get("firm_name") or "",
            subject=subject,
            message=message,
            access_url=access_url,
            expires_hours=expires_hours,
            filename=doc["filename"],
        )

    email_utils.sender_delivery_confirmed(
        to_email=user["email"],
        sender_name=user["full_name"],
        recipient_email=recipient_email,
        subject=subject,
        filename=doc["filename"],
        cert_url=cert_url or "#",
        sha256=doc["local_sha256"],
        delivery_url=f"{BASE_URL}/deliveries/{delivery_id}",
        tier=tier,
    )

    return RedirectResponse(f"/deliveries/{delivery_id}?sent=1", status_code=302)


@app.get("/deliveries/{delivery_id}", response_class=HTMLResponse)
async def delivery_detail(request: Request, delivery_id: str, sent: int = 0):
    user = await require_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        d = await conn.fetchrow(
            "SELECT * FROM deliveries WHERE id=$1 AND sender_id=$2",
            delivery_id, str(user["id"]),
        )
        if not d:
            raise HTTPException(404, "Delivery not found.")
        events = await conn.fetch(
            "SELECT * FROM delivery_events WHERE delivery_id=$1 ORDER BY created_at ASC",
            delivery_id,
        )
    return templates.TemplateResponse(
        "deliveries/detail.html",
        await ctx(request, d=dict(d), events=[dict(e) for e in events],
                  access_url=f"{BASE_URL}/r/{d['access_token']}",
                  tier_labels=TIER_LABELS, sent=sent),
    )


@app.post("/deliveries/{delivery_id}/pay")
async def create_payment_intent(request: Request, delivery_id: str):
    user = await require_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        d = await conn.fetchrow(
            "SELECT * FROM deliveries WHERE id=$1 AND sender_id=$2",
            delivery_id, str(user["id"]),
        )
    if not d:
        raise HTTPException(404)
    intent = stripe.PaymentIntent.create(
        amount=d["price_cents"],
        currency="usd",
        metadata={"delivery_id": delivery_id, "user_id": str(user["id"]), "tier": d["tier"]},
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE deliveries SET stripe_payment_intent_id=$1 WHERE id=$2",
            intent["id"], delivery_id,
        )
    return JSONResponse({"client_secret": intent["client_secret"]})


@app.get("/r/{token}", response_class=HTMLResponse)
async def recipient_link(request: Request, token: str):
    delivery_id = decode_delivery_token(token, max_age_seconds=LINK_EXPIRY_HOURS * 3600)
    if not delivery_id:
        return templates.TemplateResponse("recipient/expired.html", await ctx(request), status_code=410)

    pool = await get_pool()
    async with pool.acquire() as conn:
        d = await conn.fetchrow("SELECT * FROM deliveries WHERE id=$1", delivery_id)
        if not d:
            raise HTTPException(404)
        d = dict(d)
        if d["require_account"]:
            return RedirectResponse(f"/recipient/login?token={token}", status_code=302)
        if d["expires_at"] < datetime.now(timezone.utc):
            return templates.TemplateResponse("recipient/expired.html", await ctx(request, d=d), status_code=410)

        ip = client_ip(request)
        ua = request.headers.get("user-agent", "")

        if not d["opened_at"]:
            await conn.execute(
                "UPDATE deliveries SET status='opened', opened_at=NOW(), opened_ip=$1 WHERE id=$2",
                ip, delivery_id,
            )
            await log_event(conn, delivery_id, "OPENED", ip=ip,
                            notes="Recipient opened via link", user_agent=ua)
            if d["tier"] in ("certified", "custody") and d["certificate_id"]:
                try:
                    await evidentix_client.log_event(
                        delivery_id=delivery_id, event_type="OPENED",
                        ip_address=ip, recipient_email=d["recipient_email"],
                        certificate_id=d["certificate_id"],
                    )
                except evidentix_client.EvidentixError as e:
                    logger.error("Evidentix OPENED event failed: %s", e)

        d = dict(await conn.fetchrow("SELECT * FROM deliveries WHERE id=$1", delivery_id))

    download_url = None
    if d["allow_download"]:
        download_url = s3_utils.presigned_download_url(d["s3_key"], d["filename"], expiry=3600)

    return templates.TemplateResponse(
        "recipient/view.html",
        await ctx(request, d=d, download_url=download_url, token=token),
    )


@app.post("/r/{token}/acknowledge")
async def recipient_acknowledge(request: Request, token: str):
    delivery_id = decode_delivery_token(token, max_age_seconds=LINK_EXPIRY_HOURS * 3600)
    if not delivery_id:
        raise HTTPException(410, "Link expired.")

    pool = await get_pool()
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    async with pool.acquire() as conn:
        d = dict(await conn.fetchrow("SELECT * FROM deliveries WHERE id=$1", delivery_id))
        if not d["acknowledged_at"]:
            await conn.execute(
                "UPDATE deliveries SET status='acknowledged', acknowledged_at=NOW() WHERE id=$1",
                delivery_id,
            )
            await log_event(conn, delivery_id, "ACKNOWLEDGED", ip=ip,
                            notes="Recipient acknowledged receipt", user_agent=ua)
            if d["tier"] in ("certified", "custody") and d["certificate_id"]:
                try:
                    await evidentix_client.log_event(
                        delivery_id=delivery_id, event_type="ACKNOWLEDGED",
                        ip_address=ip, recipient_email=d["recipient_email"],
                        certificate_id=d["certificate_id"],
                    )
                except evidentix_client.EvidentixError as e:
                    logger.error("Evidentix ACKNOWLEDGED event failed: %s", e)

            sender = await conn.fetchrow("SELECT * FROM users WHERE id=$1", str(d["sender_id"]))
            if sender:
                d_fresh = dict(await conn.fetchrow("SELECT * FROM deliveries WHERE id=$1", delivery_id))
                email_utils.sender_receipt_confirmed(
                    to_email=sender["email"],
                    recipient_email=d["recipient_email"],
                    subject=d["subject"],
                    opened_at=fmt_dt(d_fresh.get("opened_at")),
                    acknowledged_at=fmt_dt(d_fresh.get("acknowledged_at")),
                    delivery_url=f"{BASE_URL}/deliveries/{delivery_id}",
                    custody_record_url=d.get("custody_record_url"),
                )

    return RedirectResponse(f"/r/{token}?ack=1", status_code=302)


@app.get("/recipient/register", response_class=HTMLResponse)
async def recipient_register_page(request: Request, token: str):
    delivery_id = decode_delivery_token(token, max_age_seconds=LINK_EXPIRY_HOURS * 3600)
    if not delivery_id:
        return templates.TemplateResponse("recipient/expired.html", await ctx(request))
    pool = await get_pool()
    async with pool.acquire() as conn:
        d = await conn.fetchrow(
            "SELECT subject, recipient_email FROM deliveries WHERE id=$1", delivery_id
        )
    return templates.TemplateResponse(
        "recipient/register.html",
        await ctx(request, d=dict(d) if d else {}, token=token),
    )


@app.post("/recipient/register")
async def recipient_register_post(
    request:   Request,
    token:     str = Form(...),
    full_name: str = Form(...),
    email:     str = Form(...),
    password:  str = Form(...),
):
    delivery_id = decode_delivery_token(token, max_age_seconds=LINK_EXPIRY_HOURS * 3600)
    if not delivery_id:
        raise HTTPException(410, "Link expired.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        d = await conn.fetchrow("SELECT * FROM deliveries WHERE id=$1", delivery_id)
        if not d:
            raise HTTPException(404)
        if d["recipient_email"] != email.lower().strip():
            return templates.TemplateResponse(
                "recipient/register.html",
                await ctx(request, error="Email must match the one this document was sent to.",
                          d=dict(d), token=token),
            )
        rec = await conn.fetchrow("SELECT id FROM recipients WHERE email=$1", email.lower())
        if not rec:
            rec = await conn.fetchrow(
                """INSERT INTO recipients (email, password_hash, full_name)
                   VALUES ($1,$2,$3) RETURNING id""",
                email.lower(), hash_password(password), full_name,
            )
        await conn.execute(
            "UPDATE deliveries SET recipient_id=$1 WHERE id=$2", str(rec["id"]), delivery_id,
        )
    return RedirectResponse(f"/r/{token}", status_code=302)


@app.get("/recipient/login", response_class=HTMLResponse)
async def recipient_login_page(request: Request, token: str):
    return templates.TemplateResponse("recipient/login.html", await ctx(request, token=token))


@app.post("/recipient/login")
async def recipient_login_post(
    request:  Request,
    token:    str = Form(...),
    email:    str = Form(...),
    password: str = Form(...),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rec = await conn.fetchrow("SELECT * FROM recipients WHERE email=$1", email.lower())
    if not rec or not rec["password_hash"] or not verify_password(password, rec["password_hash"]):
        return templates.TemplateResponse(
            "recipient/login.html",
            await ctx(request, token=token, error="Invalid email or password."),
        )
    return RedirectResponse(f"/r/{token}", status_code=302)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "Invalid webhook")
    if event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE deliveries SET paid=TRUE, paid_at=NOW() WHERE stripe_payment_intent_id=$1",
                pi["id"],
            )
    return JSONResponse({"status": "ok"})


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    return templates.TemplateResponse("notifications.html", await ctx(request))