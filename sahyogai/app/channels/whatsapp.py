"""WhatsApp channel — a thin ADAPTER, not a new pipeline.

The router takes text and returns text, so a new channel is a small wrapper.
A webhook means Twilio PUSHES the message to us; we do not poll. We expose a
URL and they POST to it. (ngrok gives your laptop a public URL so Twilio can
reach it during development.)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Response
from sqlalchemy.orm import Session

from app.agents.router import handle_query
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/channels", tags=["channels"])


def _twiml(message: str) -> Response:
    safe = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@router.post("/whatsapp")
async def whatsapp_webhook(
    From: str = Form(""),
    Body: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    phone = From.replace("whatsapp:", "").strip()
    body = (Body or "").strip()

    user = db.query(User).filter(User.phone_number == phone).first()
    if user is None:
        return _twiml(
            "You're not registered yet. Sign up on SahyogAI with this number, "
            "then message again."
        )

    # Opt-out must work from the channel itself, not only from a web page.
    if body.upper() in {"STOP", "DELETE"}:
        db.delete(user)
        db.commit()
        return _twiml("Your data has been deleted. Reply START to sign up again.")

    if not body:
        return _twiml("Ask me anything about your money, e.g. 'where is my money going?'")

    result = handle_query(db, user.id, body, channel="whatsapp")

    # WhatsApp messages have a practical length limit.
    answer = result["answer"]
    if len(answer) > 1500:
        answer = answer[:1490] + "..."
    return _twiml(answer)
