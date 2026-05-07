# ABOUTME: Shared pytest fixtures — fake ReachyMini singleton and a tiny MP3 fixture.
# ABOUTME: HTTP mocking is handled per-test via pytest-httpx's httpx_mock fixture.

from __future__ import annotations

import io
import shutil
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture
def fake_mini(monkeypatch):
    """Inject a MagicMock as the ReachyMini singleton in src.robot."""
    from src import robot

    mini = MagicMock(name="ReachyMini")
    mini.media.get_frame.return_value = np.zeros((4, 4, 3), dtype=np.uint8)
    mini.media.get_output_audio_samplerate.return_value = 48000
    mini.media.push_audio_sample = MagicMock()

    monkeypatch.setattr(robot, "_mini", mini)
    return mini


@pytest.fixture
def daemon_url(monkeypatch):
    """Pin the daemon URL to a deterministic value for httpx_mock matching."""
    url = "http://test-daemon:8321/api"
    monkeypatch.setenv("REACHY_DAEMON_URL", url)
    return url


@pytest.fixture
def tiny_mp3() -> bytes:
    """A real, decodable MP3 (200 ms of 440 Hz sine). Skip if ffmpeg is missing."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required for MP3 fixture")
    pytest.importorskip("pydub")
    from pydub.generators import Sine

    sine = Sine(440).to_audio_segment(duration=200)
    buf = io.BytesIO()
    sine.export(buf, format="mp3", bitrate="32k")
    return buf.getvalue()
