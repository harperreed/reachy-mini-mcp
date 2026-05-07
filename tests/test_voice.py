# ABOUTME: Unit tests for src.voice — TTS error handling and MP3 → PCM decoding.
# ABOUTME: ElevenLabs HTTP is fully mocked; pydub/ffmpeg is exercised via tiny_mp3 fixture.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src import voice


def test_tts_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(voice.VoiceError, match="ELEVENLABS_API_KEY"):
        voice.tts("hello")


def test_tts_calls_sdk_with_resolved_voice(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "env-voice-id")

    fake_client = MagicMock()
    fake_client.text_to_speech.convert.return_value = iter([b"chunk-a", b"chunk-b"])

    with patch("elevenlabs.ElevenLabs", return_value=fake_client) as ctor:
        out = voice.tts("hello there")

    assert out == b"chunk-achunk-b"
    ctor.assert_called_once()
    kwargs = fake_client.text_to_speech.convert.call_args.kwargs
    assert kwargs["voice_id"] == "env-voice-id"
    assert kwargs["text"] == "hello there"
    assert kwargs["model_id"] == voice.DEFAULT_MODEL


def test_tts_explicit_voice_id_wins_over_env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "env-voice")

    fake_client = MagicMock()
    fake_client.text_to_speech.convert.return_value = iter([b""])

    with patch("elevenlabs.ElevenLabs", return_value=fake_client):
        voice.tts("hi", voice_id="explicit-voice")

    assert fake_client.text_to_speech.convert.call_args.kwargs["voice_id"] == "explicit-voice"


def test_tts_wraps_sdk_failure_as_voice_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    fake_client = MagicMock()
    fake_client.text_to_speech.convert.side_effect = RuntimeError("api boom")

    with (
        patch("elevenlabs.ElevenLabs", return_value=fake_client),
        pytest.raises(voice.VoiceError, match="api boom"),
    ):
        voice.tts("hi")


def test_mp3_to_pcm_decodes_to_mono_float32(tiny_mp3):
    pcm = voice.mp3_to_pcm(tiny_mp3, sample_rate=48000)
    assert pcm.dtype == np.float32
    assert pcm.ndim == 1
    # 200 ms at 48 kHz is roughly 9600 samples; allow generous slack for codec padding.
    assert 8000 <= len(pcm) <= 12000
    assert pcm.max() <= 1.0
    assert pcm.min() >= -1.0


def test_mp3_to_pcm_resamples(tiny_mp3):
    pcm_lo = voice.mp3_to_pcm(tiny_mp3, sample_rate=16000)
    pcm_hi = voice.mp3_to_pcm(tiny_mp3, sample_rate=48000)
    # Roughly 3x the samples at 48 kHz vs 16 kHz.
    assert len(pcm_hi) > 2 * len(pcm_lo)


def test_mp3_to_pcm_garbage_raises():
    with pytest.raises(voice.VoiceError):
        voice.mp3_to_pcm(b"definitely not an mp3 file", sample_rate=48000)


def test_estimate_speech_seconds_floor():
    assert voice.estimate_speech_seconds("hi") == 1.0
    assert voice.estimate_speech_seconds("a" * 30) == 2.0
