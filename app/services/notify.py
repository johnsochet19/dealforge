"""Notification delivery -- the dispatcher that consumes AlertEvents.

Alert evaluation (``alerts.py``) only *records* that something fired; turning
that record into an email or webhook is a deliberately separate concern. This
module is the dispatcher the README promised: it reads a user's configured
``NotificationChannel`` rows and delivers one message per channel.

Channels are pluggable in the same spirit as the connector registry -- each
knows how to deliver one message to one target, and new channels register
themselves without any change to rule or evaluation logic.

Delivery is best-effort and isolated: a channel that raises records a failure
on the event and never breaks alert evaluation. The per-event ``delivery_status``
(no_channels | sent | partial | failed) plus a JSON ``delivery_detail`` are the
audit trail.

The actual network calls go through the module-level ``_http_post`` and
``_smtp_send`` seams so tests can exercise the whole path without a network.
"""
import json
import os
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from ..models import NotificationChannel


class DeliveryError(Exception):
    """Raised by a channel when delivery fails. Caught by the dispatcher."""


# --- network seams (monkeypatchable in tests) -------------------------------

def _http_post(url: str, payload: dict, timeout: float = 5.0) -> int:
    """POST JSON to a webhook. Returns the HTTP status code."""
    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.status_code


def _smtp_send(msg: EmailMessage, *, host: str, port: int, user: str | None,
               password: str | None, use_tls: bool, timeout: float = 10.0) -> None:
    with smtplib.SMTP(host, port, timeout=timeout) as server:
        if use_tls:
            server.starttls()
        if user:
            server.login(user, password or "")
        server.send_message(msg)


# --- channels ---------------------------------------------------------------

class Channel(ABC):
    kind: str = "base"

    @abstractmethod
    def send(self, target: str, *, subject: str, body: str, payload: dict) -> None:
        """Deliver one message to one target. Raise DeliveryError on failure."""


class WebhookChannel(Channel):
    kind = "webhook"

    def send(self, target, *, subject, body, payload):
        try:
            _http_post(target, payload)
        except Exception as exc:  # httpx errors, timeouts, non-2xx
            raise DeliveryError(f"webhook POST failed: {exc}") from exc


class EmailChannel(Channel):
    """SMTP email. Configured entirely from the environment so no secrets live
    in the DB. If SMTP isn't configured, delivery fails loudly (and is recorded)
    rather than silently dropping the alert."""
    kind = "email"

    def send(self, target, *, subject, body, payload):
        host = os.getenv("SMTP_HOST")
        if not host:
            raise DeliveryError("email channel not configured (set SMTP_HOST)")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM", "alerts@dealforge.local")
        msg["To"] = target
        msg.set_content(body)
        try:
            _smtp_send(
                msg,
                host=host,
                port=int(os.getenv("SMTP_PORT", "587")),
                user=os.getenv("SMTP_USER") or None,
                password=os.getenv("SMTP_PASSWORD") or None,
                use_tls=os.getenv("SMTP_STARTTLS", "true").lower() != "false",
            )
        except DeliveryError:
            raise
        except Exception as exc:
            raise DeliveryError(f"SMTP send failed: {exc}") from exc


_CHANNELS: dict[str, Channel] = {}


def register_channel(channel: Channel) -> None:
    _CHANNELS[channel.kind] = channel


def get_channel(kind: str) -> Channel | None:
    return _CHANNELS.get(kind)


def channel_kinds() -> list[str]:
    return sorted(_CHANNELS)


register_channel(WebhookChannel())
register_channel(EmailChannel())


# --- dispatch ---------------------------------------------------------------

def _build_payload(alert, event, product) -> dict:
    return {
        "event": "alert_fired",
        "alert_id": alert.id,
        "product_id": alert.product_id,
        "product_title": getattr(product, "title", None),
        "product_url": getattr(product, "url", None),
        "rule_type": alert.rule_type,
        "threshold": alert.threshold,
        "message": event.message,
        "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
    }


def dispatch(db, alert, event, product=None) -> str:
    """Deliver ``event`` to every active channel the alert's user configured.

    Mutates ``event.delivery_status`` / ``event.delivery_detail`` and returns
    the status. Does not commit -- the caller owns the transaction.
    """
    channels = db.execute(
        select(NotificationChannel).where(
            NotificationChannel.user_email == alert.user_email,
            NotificationChannel.active.is_(True),
        )
    ).scalars().all()

    if not channels:
        event.delivery_status = "no_channels"
        event.delivery_detail = None
        return event.delivery_status

    title = getattr(product, "title", None) or f"product {alert.product_id}"
    subject = f"DealForge alert: {title}"
    payload = _build_payload(alert, event, product)

    results = []
    for ch in channels:
        impl = get_channel(ch.kind)
        if impl is None:
            results.append({"kind": ch.kind, "target": ch.target,
                            "status": "failed", "detail": "unknown channel kind"})
            continue
        try:
            impl.send(ch.target, subject=subject, body=event.message, payload=payload)
            results.append({"kind": ch.kind, "target": ch.target, "status": "sent"})
        except Exception as exc:  # DeliveryError and anything unexpected
            results.append({"kind": ch.kind, "target": ch.target,
                            "status": "failed", "detail": str(exc)})

    sent = sum(1 for r in results if r["status"] == "sent")
    if sent == len(results):
        event.delivery_status = "sent"
    elif sent == 0:
        event.delivery_status = "failed"
    else:
        event.delivery_status = "partial"
    event.delivery_detail = json.dumps(results)
    return event.delivery_status
