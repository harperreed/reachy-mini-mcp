# ABOUTME: ElevenLabs TTS and MP3 → float32 PCM decoding for the Reachy Mini robot.
# ABOUTME: Pure functions — no robot SDK knowledge here; that lives in robot.py.

from __future__ import annotations

import io
import os

import numpy as np

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # Rachel (ElevenLabs default)
DEFAULT_MODEL = "eleven_flash_v2_5"        # low-latency multilingual model
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class VoiceError(RuntimeError):
    """ElevenLabs auth, network, or decoding failure."""


def _resolve_voice(voice_id: str | None) -> str:
    if voice_id:
        return voice_id
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def tts(text: str, voice_id: str | None = None, model: str | None = None) -> bytes:
    """Synthesize text via ElevenLabs and return MP3 bytes.

    Reads ELEVENLABS_API_KEY from the environment (handled by the SDK).
    """
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise VoiceError("ELEVENLABS_API_KEY not set")

    try:
        from elevenlabs import ElevenLabs
    except ImportError as e:
        raise VoiceError(f"elevenlabs SDK not installed: {e}") from e

    try:
        client = ElevenLabs()
        chunks = client.text_to_speech.convert(
            voice_id=_resolve_voice(voice_id),
            model_id=model or DEFAULT_MODEL,
            text=text,
            output_format=DEFAULT_OUTPUT_FORMAT,
        )
        return b"".join(chunks)
    except Exception as e:
        raise VoiceError(f"ElevenLabs TTS failed: {e}") from e


def mp3_to_pcm(mp3_bytes: bytes, sample_rate: int) -> np.ndarray:
    """Decode MP3 bytes to mono float32 PCM at the given sample rate.

    Requires ffmpeg on PATH (used by pydub).
    Returns shape ``(num_samples,)`` with values in [-1.0, 1.0].
    """
    try:
        from pydub import AudioSegment
    except ImportError as e:
        raise VoiceError(f"pydub not installed: {e}") from e

    try:
        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    except Exception as e:
        raise VoiceError(f"could not decode MP3 ({len(mp3_bytes)} bytes): {e}") from e

    seg = seg.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    raw = np.frombuffer(seg.raw_data, dtype=np.int16)
    return raw.astype(np.float32) / 32768.0


def estimate_speech_seconds(text: str) -> float:
    """Conservative ~15 chars/sec estimate, used only as a safety upper bound."""
    return max(1.0, len(text) / 15.0)
