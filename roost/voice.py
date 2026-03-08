"""Voice note transcription using faster-whisper (lazy-loaded).

The model is only loaded into memory when a voice note arrives.
After 5 minutes of inactivity it unloads automatically to free ~1GB RAM.
"""

import logging
import os
import tempfile
import threading

logger = logging.getLogger("roost.voice")

_UNLOAD_SECONDS = 300  # 5 minutes

# Module-level state
_model = None
_unload_timer: threading.Timer | None = None
_lock = threading.Lock()


def _unload_model():
    """Drop the model from memory after idle timeout."""
    global _model
    with _lock:
        if _model is not None:
            _model = None
            import gc, ctypes
            gc.collect()
            try:
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                logger.debug("malloc_trim call failed (non-glibc system)", exc_info=True)
            logger.info("Whisper model unloaded (idle %ds).", _UNLOAD_SECONDS)


def _reset_timer():
    """Reset the auto-unload countdown."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(_UNLOAD_SECONDS, _unload_model)
    _unload_timer.daemon = True
    _unload_timer.start()


def _get_model():
    """Lazy-load the faster-whisper model, reset unload timer."""
    global _model
    with _lock:
        if _model is not None:
            _reset_timer()
            return _model

        logger.info("Loading faster-whisper model (base, INT8)...")
        from faster_whisper import WhisperModel

        _model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("faster-whisper model loaded.")
        _reset_timer()
        return _model


def transcribe_file(audio_path: str) -> str:
    """Transcribe an audio file and return the text.

    Args:
        audio_path: Path to an audio file (OGG, MP3, WAV, etc.)

    Returns:
        Transcribed text string, or empty string on failure.
    """
    try:
        model = _get_model()
        # Prompt hints bias Whisper toward recognising command prefixes
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            initial_prompt="task: journal: note:",
        )
        text = " ".join(seg.text for seg in segments).strip()
        logger.info(
            "Transcribed %.1fs audio (%s) -> %d chars",
            info.duration, info.language, len(text),
        )
        return text
    except Exception:
        logger.exception("Transcription failed for %s", audio_path)
        return ""


async def transcribe_telegram_voice(voice_file) -> str:
    """Download a Telegram voice file and transcribe it.

    Args:
        voice_file: telegram.File object from bot.get_file()

    Returns:
        Transcribed text string.
    """
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await voice_file.download_to_drive(tmp_path)
        return transcribe_file(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
