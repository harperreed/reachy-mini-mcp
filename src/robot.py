# ABOUTME: Reachy Mini SDK lifecycle + daemon REST client + media (camera/audio) helpers.
# ABOUTME: All robot I/O lives here so server.py can stay a thin MCP shim.

from __future__ import annotations

import atexit
import io
import math
import os
import threading
import time
from typing import Any

import httpx
import numpy as np

DEFAULT_DAEMON_URL = "http://reachy-mini.local:8321/api"
_AUDIO_CHUNK_SIZE = 960  # 20 ms at 48 kHz, padded to actual rate at runtime


class RobotError(RuntimeError):
    """Base for robot-side failures surfaced to MCP tool callers."""


class DaemonError(RobotError):
    """Daemon REST API unreachable, returned non-2xx, or returned malformed payload."""


class MediaError(RobotError):
    """Camera capture or audio playback failed via the SDK."""


# --------------------------------------------------------------------------- #
# Daemon REST client                                                          #
# --------------------------------------------------------------------------- #


def daemon_url() -> str:
    return os.environ.get("REACHY_DAEMON_URL", DEFAULT_DAEMON_URL).rstrip("/")


def daemon_post(
    path: str,
    json: dict | None = None,
    timeout: float = 30.0,
    retries: int = 1,
) -> Any:
    """POST to the daemon. Retries once on connect error. Raises DaemonError on failure."""
    url = f"{daemon_url()}{path}"
    last_error: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            r = httpx.post(url, json=json, timeout=timeout)
            r.raise_for_status()
            return r.json() if r.content else None
        except httpx.ConnectError as e:
            last_error = e
            continue
        except httpx.HTTPStatusError as e:
            raise DaemonError(
                f"daemon {e.response.status_code} on POST {path}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise DaemonError(f"daemon request failed on POST {path}: {e}") from e
    raise DaemonError(
        f"cannot reach daemon at {url}: {last_error}. "
        f"Is the robot powered on and on the network?"
    )


def daemon_get(path: str, timeout: float = 10.0, retries: int = 1) -> Any:
    """GET from the daemon. Retries once on connect error. Raises DaemonError on failure."""
    url = f"{daemon_url()}{path}"
    last_error: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            r = httpx.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError as e:
            last_error = e
            continue
        except httpx.HTTPStatusError as e:
            raise DaemonError(
                f"daemon {e.response.status_code} on GET {path}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise DaemonError(f"daemon request failed on GET {path}: {e}") from e
    raise DaemonError(f"cannot reach daemon at {url}: {last_error}")


# --------------------------------------------------------------------------- #
# Movement (daemon REST)                                                      #
# --------------------------------------------------------------------------- #

MOVE_LIBRARIES = {
    "emotions": "pollen-robotics/reachy-mini-emotions-library",
    "dances": "pollen-robotics/reachy-mini-dances-library",
}


def goto(
    z: float = 0.0,
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
    antennas: tuple[float, float] | None = None,
    duration: float = 1.0,
    interpolation: str = "minjerk",
) -> None:
    """Move head (degrees, converted to radians) and optionally antennas (degrees)."""
    body: dict[str, Any] = {
        "head_pose": {
            "x": 0.0,
            "y": 0.0,
            "z": z,
            "roll": math.radians(roll),
            "pitch": math.radians(pitch),
            "yaw": math.radians(yaw),
        },
        "duration": duration,
        "interpolation": interpolation,
    }
    if antennas is not None:
        body["antennas"] = [math.radians(antennas[0]), math.radians(antennas[1])]
    daemon_post("/move/goto", json=body)


def play_move(name: str, library: str = "emotions") -> dict[str, Any]:
    """Trigger a recorded move by name. Raises DaemonError with a clear message on 404."""
    dataset = MOVE_LIBRARIES.get(library)
    if dataset is None:
        raise DaemonError(f"unknown library {library!r}; choices: {list(MOVE_LIBRARIES)}")
    try:
        return daemon_post(
            f"/move/play/recorded-move-dataset/{dataset}/{name}",
            timeout=30.0,
        ) or {}
    except DaemonError as e:
        if "404" in str(e):
            raise DaemonError(
                f"move {name!r} not found in {library!r}. Use discover() to list options."
            ) from e
        raise


def list_moves(library: str = "emotions") -> list[str]:
    dataset = MOVE_LIBRARIES.get(library)
    if dataset is None:
        raise DaemonError(f"unknown library {library!r}; choices: {list(MOVE_LIBRARIES)}")
    moves = daemon_get(f"/move/recorded-move-datasets/list/{dataset}")
    if not isinstance(moves, list):
        raise DaemonError(f"unexpected /list response: {moves!r}")
    return moves


def wake() -> None:
    daemon_post("/move/play/wake_up")


def sleep_pose() -> None:
    daemon_post("/move/play/goto_sleep")


def wait_for_moves(timeout: float = 30.0, poll_interval: float = 0.1) -> bool:
    """Poll /move/running until empty or timeout. Returns True if drained, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            running = daemon_get("/move/running", timeout=2.0, retries=0)
            if isinstance(running, list) and not running:
                return True
        except DaemonError:
            pass  # transient — keep polling
        time.sleep(poll_interval)
    return False


# --------------------------------------------------------------------------- #
# SDK lifecycle (lazy singleton)                                              #
# --------------------------------------------------------------------------- #

_mini: Any = None
_mini_lock = threading.Lock()


def _build_mini() -> Any:
    """Construct the ReachyMini singleton with WebRTC media backend."""
    try:
        from reachy_mini import ReachyMini
    except ImportError as e:
        raise MediaError(f"reachy-mini SDK not installed: {e}") from e

    connection_mode = "network"
    if os.environ.get("REACHY_LOCALHOST_ONLY"):
        connection_mode = "localhost_only"

    try:
        return ReachyMini(
            connection_mode=connection_mode,
            media_backend="webrtc",
            log_level="WARNING",
        )
    except Exception as e:
        raise MediaError(f"failed to connect to robot via SDK: {e}") from e


def get_mini() -> Any:
    """Return the lazily-initialized ReachyMini singleton."""
    global _mini
    if _mini is None:
        with _mini_lock:
            if _mini is None:
                _mini = _build_mini()
    return _mini


def disconnect() -> None:
    """Close the SDK connection if one is open. Safe to call multiple times."""
    global _mini
    with _mini_lock:
        if _mini is None:
            return
        try:
            close = getattr(_mini, "__exit__", None)
            if close is not None:
                close(None, None, None)
            elif hasattr(_mini, "client"):
                _mini.client.disconnect()
        except Exception:
            pass
        _mini = None


atexit.register(disconnect)


# --------------------------------------------------------------------------- #
# Media (camera + audio via SDK)                                              #
# --------------------------------------------------------------------------- #


def get_frame_jpeg() -> bytes:
    """Capture a frame and JPEG-encode it. Returns the encoded bytes."""
    mini = get_mini()
    frame = mini.media.get_frame()
    if frame is None:
        raise MediaError("camera returned no frame (is the robot streaming video?)")
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise MediaError(f"unexpected frame shape: {getattr(frame, 'shape', None)}")
    try:
        from PIL import Image
    except ImportError as e:
        raise MediaError(f"Pillow not installed: {e}") from e
    # SDK returns BGR; PIL expects RGB.
    rgb = frame[..., ::-1]
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def output_sample_rate() -> int:
    """Query the WebRTC audio output sample rate from the SDK."""
    mini = get_mini()
    try:
        rate = mini.media.get_output_audio_samplerate()
    except Exception as e:
        raise MediaError(f"could not read audio sample rate: {e}") from e
    if not isinstance(rate, int) or rate <= 0:
        raise MediaError(f"invalid sample rate from SDK: {rate!r}")
    return rate


def play_pcm(pcm: np.ndarray, sample_rate: int) -> None:
    """Push float32 mono PCM to the robot's speaker over WebRTC, blocking for duration.

    The SDK's push_audio_sample is non-blocking (queues into GStreamer), so we sleep
    for len(pcm)/sample_rate seconds afterwards to keep callers in sync for choreography.
    """
    if pcm.dtype != np.float32:
        pcm = pcm.astype(np.float32)
    if pcm.ndim != 1:
        raise MediaError(f"play_pcm expects mono (1-D) PCM, got shape {pcm.shape}")

    mini = get_mini()
    chunk = max(_AUDIO_CHUNK_SIZE, sample_rate // 50)  # ~20 ms
    try:
        for i in range(0, len(pcm), chunk):
            mini.media.push_audio_sample(pcm[i : i + chunk])
    except Exception as e:
        raise MediaError(f"audio push failed: {e}") from e

    time.sleep(len(pcm) / sample_rate)
