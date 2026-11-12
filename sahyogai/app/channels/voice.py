"""Voice channel — a WRAPPER, not a new system.

    text   = transcribe(audio)                      # Whisper: speech -> text
    result = handle_query(db, user_id, text, ...)   # the SAME router
    speech = synthesize_speech(result["answer"])    # gTTS: text -> speech

The middle line is identical to the web and WhatsApp paths. If adding a
channel had required touching the agents, the boundary would be in the
wrong place.
"""

from __future__ import annotations

import io
import logging
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.router import handle_query
from app.database import get_db
from app.models import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])

_whisper_model = None


def transcribe(audio_bytes: bytes) -> str | None:
    """Speech -> text. Returns None if Whisper is not installed."""
    global _whisper_model
    try:
        import whisper
    except ImportError:
        log.info("Whisper not installed; voice input unavailable.")
        return None

    try:
        if _whisper_model is None:
            _whisper_model = whisper.load_model("base")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            return _whisper_model.transcribe(tmp.name).get("text", "").strip()
    except Exception as exc:
        log.warning("Transcription failed: %s", exc)
        return None


def synthesize_speech(text: str, language: str = "en") -> bytes | None:
    """Text -> speech. Returns None if gTTS is not installed."""
    try:
        from gtts import gTTS
    except ImportError:
        log.info("gTTS not installed; voice output unavailable.")
        return None

    try:
        buffer = io.BytesIO()
        gTTS(text=text[:1000], lang=language).write_to_fp(buffer)
        buffer.seek(0)
        return buffer.read()
    except Exception as exc:
        log.warning("Speech synthesis failed: %s", exc)
        return None


@router.post("/voice/{user_id}")
async def voice_query(
    user_id: int,
    audio: UploadFile = File(...),
    want_audio: bool = Form(True),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")

    text = transcribe(await audio.read())
    if not text:
        raise HTTPException(
            400,
            "Could not transcribe the audio. Whisper may not be installed, or the clip was empty.",
        )

    result = handle_query(db, user_id, text, channel="voice")   # the SAME router

    if want_audio:
        speech = synthesize_speech(result["answer"], user.language or "en")
        if speech:
            return StreamingResponse(
                io.BytesIO(speech),
                media_type="audio/mpeg",
                headers={"X-Transcript": text[:200], "X-Tool-Used": result["tool_used"]},
            )

    return {"transcript": text, **result}


@router.post("/voice/{user_id}/text")
async def voice_text_only(
    user_id: int, text: str = Form(...), db: Session = Depends(get_db)
) -> dict:
    """Same path without audio — useful for testing the voice flow when
    Whisper isn't installed."""
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")
    return {"transcript": text, **handle_query(db, user_id, text, channel="voice")}
