import os
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

EVIDENTIX_URL = os.environ.get("EVIDENTIX_API_URL", "https://evidenceanalyzer.com")
EVIDENTIX_KEY = os.environ.get("EVIDENTIX_API_KEY", "")
TIMEOUT = 90


class EvidentixError(Exception):
    pass


def _headers():
    return {"x-api-key": EVIDENTIX_KEY, "Content-Type": "application/json"}


async def certify(
    *,
    s3_presigned_url: str,
    filename: str,
    delivery_id: str,
    sender_id: str,
    recipient_email: str,
    matter_ref: Optional[str] = None,
) -> dict:
    payload = {
        "s3_url":      s3_presigned_url,
        "filename":    filename,
        "case_id":     f"ld-delivery-{delivery_id}",
        "task_id":     delivery_id,
        "provider_id": sender_id,
        "notes": (
            f"LegalDrop delivery {delivery_id} | "
            f"Sender: {sender_id} | Recipient: {recipient_email}"
            + (f" | Matter: {matter_ref}" if matter_ref else "")
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{EVIDENTIX_URL}/api/v1/certify",
                headers=_headers(),
                json=payload,
            )
        if r.status_code != 200:
            raise EvidentixError(f"/certify {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.RequestError as e:
        raise EvidentixError(f"Connection error: {e}") from e


async def log_event(
    *,
    delivery_id: str,
    event_type: str,
    ip_address: str,
    recipient_email: str,
    certificate_id: Optional[str] = None,
) -> dict:
    payload = {
        "case_id":    f"ld-delivery-{delivery_id}",
        "event_type": f"LEGALDROP_{event_type}",
        "user":       recipient_email,
        "ip_address": ip_address,
        "notes": (
            f"LegalDrop delivery {delivery_id} | "
            f"Event: {event_type} | Recipient: {recipient_email}"
            + (f" | Cert: {certificate_id}" if certificate_id else "")
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{EVIDENTIX_URL}/api/v1/custody-event",
                headers=_headers(),
                json=payload,
            )
        if r.status_code != 200:
            raise EvidentixError(f"/custody-event {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.RequestError as e:
        raise EvidentixError(f"Connection error: {e}") from e


async def custody_record(
    *,
    delivery_id: str,
    certificate_id: str,
    sender_name: str,
    recipient_email: str,
    filename: str,
    matter_ref: Optional[str] = None,
) -> dict:
    payload = {
        "task_id":        delivery_id,
        "submission_id":  delivery_id,
        "certificate_id": certificate_id,
        "requestor_name": sender_name,
        "provider_name":  recipient_email,
        "case_reference": matter_ref or f"LegalDrop-{delivery_id[:8]}",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{EVIDENTIX_URL}/api/v1/custody-record",
                headers=_headers(),
                json=payload,
            )
        if r.status_code != 200:
            raise EvidentixError(f"/custody-record {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.RequestError as e:
        raise EvidentixError(f"Connection error: {e}") from e


async def ping() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{EVIDENTIX_URL}/api/v1/ping", headers=_headers())
        return r.status_code == 200
    except Exception:
        return False