"""
Reachy Mini MCP Server
======================
MCP tools for controlling Pollen Robotics Reachy Mini robot.

Architecture:
  MCP Tool Call → SDK → Daemon → Robot/Simulator

7 tools (Miller's Law):
  - speak(text, listen_after)  Voice + gesture + optionally hear response
  - listen(duration)           STT via Deepgram Nova-2
  - snap()                     Camera capture (base64 JPEG)
  - show(emotion, move)        Express emotion or play recorded move
  - look(roll, pitch, yaw, z)  Head positioning
  - rest(mode)                 neutral / sleep / wake
  - discover(library)          Find available recorded moves
"""

import math
import base64
import os
from typing import Optional, Literal

import numpy as np
from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(
    name="reachy-mini",
    instructions="""
    Reachy Mini robot control for expressive robotics.

    Use these tools for robot control:
    - show() for 12 built-in emotions (curious, joy, thinking, etc.)
    - show(move=...) for 81 recorded emotions from Pollen (fear1, rage1, serenity1, etc.)
    - discover() to see available recorded moves
    - look() for precise head positioning
    - speak() to vocalize with [move:X] markers for choreography
    - listen() to hear and transcribe speech
    - snap() to capture camera images
    - rest() for neutral pose, sleep, or wake

    Prefer show() for common emotions, show(move=...) for nuanced expressions.
    """
)

# ==============================================================================
# EXPRESSION MAPPINGS
# ==============================================================================
# High-level emotions → motor choreography
# Head pose: (x, y, z, roll, pitch, yaw) - z is height, roll/pitch/yaw are degrees
# Antennas: [left, right] in degrees

EXPRESSIONS = {
    "neutral": {
        "head": {"z": 0, "roll": 0, "pitch": 0, "yaw": 0},
        "antennas": [0, 0],
        "duration": 1.5,
        "method": "minjerk"
    },
    "curious": {
        "head": {"z": 0, "roll": 0, "pitch": 10, "yaw": 8},  # Forward, slight turn
        "antennas": [20, 20],  # Both up, alert
        "duration": 1.2,
        "method": "ease_in_out"
    },
    "uncertain": {
        "head": {"z": 0, "roll": 8, "pitch": -3, "yaw": 3},  # Head tilt, slight back
        "antennas": [-15, 15],  # Asymmetric - confusion
        "duration": 2.0,
        "method": "minjerk"
    },
    "recognition": {
        "head": {"z": 0, "roll": 0, "pitch": 5, "yaw": 0},  # Slight forward - attention
        "antennas": [30, 30],  # Both high - alert/happy
        "duration": 0.8,
        "method": "cartoon"
    },
    "joy": {
        "head": {"z": 0, "roll": -3, "pitch": 8, "yaw": 0},  # Head up and forward
        "antennas": [40, 40],  # Elevated
        "duration": 1.0,
        "method": "cartoon"
    },
    "thinking": {
        "head": {"z": 0, "roll": 5, "pitch": 3, "yaw": 12},  # Tilt, look away slightly
        "antennas": [8, -8],  # Slight asymmetry
        "duration": 1.5,
        "method": "ease_in_out"
    },
    "listening": {
        "head": {"z": 0, "roll": -3, "pitch": 8, "yaw": 0},  # Attentive forward lean
        "antennas": [25, 25],  # Alert
        "duration": 1.0,
        "method": "minjerk"
    },
    "agreeing": {
        "head": {"z": 0, "roll": 0, "pitch": 8, "yaw": 0},  # Nod forward
        "antennas": [20, 20],
        "duration": 0.5,
        "method": "ease_in_out"
    },
    "disagreeing": {
        "head": {"z": 0, "roll": 0, "pitch": 0, "yaw": 12},  # Shake start
        "antennas": [-8, -8],  # Slightly down
        "duration": 0.4,
        "method": "ease_in_out"
    },
    "sleepy": {
        "head": {"z": 0, "roll": 8, "pitch": -10, "yaw": 0},  # Head droops
        "antennas": [-20, -20],  # Down
        "duration": 2.5,
        "method": "minjerk"
    },
    "surprised": {
        "head": {"z": 0, "roll": 0, "pitch": -8, "yaw": 0},  # Pull back
        "antennas": [45, 45],  # High alert
        "duration": 0.3,
        "method": "cartoon"
    },
    "focused": {
        "head": {"z": 0, "roll": 0, "pitch": 6, "yaw": 0},  # Forward, intent
        "antennas": [18, 18],  # Alert but not excited
        "duration": 1.0,
        "method": "minjerk"
    }
}


# ==============================================================================
# DAEMON HTTP CLIENT
# ==============================================================================
# All robot control flows through the daemon's REST API on the wireless robot.
# No zenoh / no in-process SDK — keeps the MCP host machine independent.

DAEMON_URL = os.environ.get("REACHY_DAEMON_URL", "http://reachy-mini.local:8000/api")


def _daemon_post(path: str, json: Optional[dict] = None, timeout: float = 30.0):
    """POST to the daemon API. Returns parsed JSON on 2xx, raises with daemon message on error."""
    import httpx
    try:
        r = httpx.post(f"{DAEMON_URL}{path}", json=json, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.content else None
    except httpx.ConnectError:
        raise RuntimeError(f"Cannot connect to daemon at {DAEMON_URL}. Is the robot powered on and on the network?")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Daemon {e.response.status_code} on POST {path}: {e.response.text}")


def _daemon_get(path: str, timeout: float = 10.0):
    """GET from the daemon API."""
    import httpx
    try:
        r = httpx.get(f"{DAEMON_URL}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        raise RuntimeError(f"Cannot connect to daemon at {DAEMON_URL}.")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Daemon {e.response.status_code} on GET {path}: {e.response.text}")


def _goto(
    z_m: float = 0.0,
    roll_deg: float = 0.0,
    pitch_deg: float = 0.0,
    yaw_deg: float = 0.0,
    antennas_deg: Optional[tuple] = None,
    duration: float = 1.0,
    interpolation: str = "minjerk",
) -> dict:
    """Issue /api/move/goto with degree-to-radian + meter unit conversions."""
    body: dict = {
        "head_pose": {
            "x": 0.0,
            "y": 0.0,
            "z": z_m,
            "roll": math.radians(roll_deg),
            "pitch": math.radians(pitch_deg),
            "yaw": math.radians(yaw_deg),
        },
        "duration": duration,
        "interpolation": interpolation,
    }
    if antennas_deg is not None:
        body["antennas"] = [math.radians(antennas_deg[0]), math.radians(antennas_deg[1])]
    return _daemon_post("/move/goto", json=body)


def _upload_and_play(audio_path: str) -> None:
    """Upload an audio file to the robot and trigger playback over REST.

    Returns when playback has been *started* (the daemon plays async; there is
    no REST endpoint to poll sound playback completion, so callers that need to
    wait should sleep for an estimated duration).
    """
    import httpx
    filename = os.path.basename(audio_path)
    with open(audio_path, "rb") as f:
        r = httpx.post(
            f"{DAEMON_URL}/media/sounds/upload",
            files={"file": (filename, f, "audio/mpeg")},
            timeout=30.0,
        )
        r.raise_for_status()
    r = httpx.post(
        f"{DAEMON_URL}/media/play_sound",
        json={"file": filename},
        timeout=10.0,
    )
    r.raise_for_status()


def _estimate_speech_duration(text: str) -> float:
    """Rough estimate of TTS duration. ~15 chars/sec is conservative for Aura/Grok."""
    return max(1.0, len(text) / 15.0)


def get_robot():
    """Stub for tools that haven't been ported to REST yet (listen/snap)."""
    raise RuntimeError(
        "This tool (mic capture or camera) requires the SDK in-process; not yet "
        "implemented in REST-only mode."
    )


def cleanup_robot():
    """No-op in REST-only mode (kept for atexit hook compatibility)."""
    return


# ==============================================================================
# MCP TOOLS
# ==============================================================================

def _do_express(emotion: str) -> str:
    """Internal helper - execute an emotion expression."""
    if emotion not in EXPRESSIONS:
        return f"Unknown emotion: {emotion}. Available: {list(EXPRESSIONS.keys())}"

    expr = EXPRESSIONS[emotion]
    head = expr["head"]
    antennas = expr["antennas"]

    try:
        _goto(
            z_m=head["z"],
            roll_deg=head["roll"],
            pitch_deg=head["pitch"],
            yaw_deg=head["yaw"],
            antennas_deg=(antennas[0], antennas[1]),
            duration=expr["duration"],
            interpolation=expr["method"],
        )
        return f"Expressed: {emotion}"
    except Exception as e:
        return f"Expression failed: {e}"


def _do_move(name: str) -> str:
    """
    Execute a move by name - tries built-in emotions first, then Pollen library.

    Built-in emotions are silent (motor-only), Pollen moves may have audio.
    """
    if name in EXPRESSIONS:
        return _do_express(name)
    return _do_play_move(name)


@mcp.tool()
def show(
    emotion: Literal[
        "neutral", "curious", "uncertain", "recognition", "joy",
        "thinking", "listening", "agreeing", "disagreeing",
        "sleepy", "surprised", "focused"
    ] = "neutral",
    move: str = ""
) -> str:
    """
    Express an emotion through physical movement.

    High-level tool that maps emotions to motor choreography.
    Caller specifies WHAT to express; tool handles HOW to move.

    Use `emotion` for 12 built-in expressions (fast, local):
    - neutral, curious, uncertain, recognition, joy
    - thinking, listening, agreeing, disagreeing
    - sleepy, surprised, focused

    Use `move` for 81 recorded emotions from Pollen (e.g., "fear1", "loving1"):
    - More nuanced, professionally choreographed
    - Use list_moves() to see all available

    Args:
        emotion: Built-in emotional state to express
        move: Recorded move name (overrides emotion if provided)

    Returns:
        Confirmation of expression executed
    """
    if move:
        return _do_play_move(move)
    return _do_express(emotion)


@mcp.tool()
def look(
    roll: float = 0,
    pitch: float = 0,
    yaw: float = 0,
    z: float = 0,
    duration: float = 1.0
) -> str:
    """
    Direct head positioning in degrees.

    Use for precise control when express() doesn't fit.
    For most cases, prefer express() for cognitive simplicity.

    Args:
        roll: Tilt left/right (-45 to 45). Positive = right ear to shoulder
        pitch: Nod up/down (-30 to 30). Positive = looking up
        yaw: Turn left/right (-90 to 90). Positive = looking right
        z: Vertical offset (-20 to 20). Positive = head higher
        duration: Movement time in seconds (0.1 to 5.0)

    Returns:
        Confirmation
    """
    # Clamp values to safe ranges (daemon also clamps; this is belt-and-braces)
    roll = max(-40, min(40, roll))
    pitch = max(-40, min(40, pitch))
    yaw = max(-180, min(180, yaw))
    z = max(-20, min(20, z))
    duration = max(0.1, min(5.0, duration))

    try:
        _goto(z_m=z, roll_deg=roll, pitch_deg=pitch, yaw_deg=yaw, duration=duration)
        return f"Head positioned: roll={roll}°, pitch={pitch}°, yaw={yaw}°, z={z}"
    except Exception as e:
        return f"Movement failed: {e}"


GROK_VOICES = ["ara", "eve", "leo", "rex", "sal"]

def text_to_speech(text: str, voice: Optional[str] = None) -> str:
    """
    Convert text to speech. Uses Grok Voice if available, falls back to Deepgram.
    Returns path to temporary audio file.
    """
    xai_key = os.environ.get("XAI_API_KEY")
    if xai_key:
        return grok_text_to_speech(text, xai_key, voice)
    return deepgram_text_to_speech(text)


def deepgram_text_to_speech(text: str) -> str:
    """Convert text to speech using Deepgram TTS (Aura 2)."""
    import tempfile
    import httpx

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY environment variable not set")

    url = "https://api.deepgram.com/v1/speak?model=aura-2-saturn-en"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }
    data = {"text": text}

    response = httpx.post(url, headers=headers, json=data, timeout=30.0)
    response.raise_for_status()

    # Save to temp file
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    temp_file.write(response.content)
    temp_file.close()

    return temp_file.name


def grok_text_to_speech(text: str, api_key: str, voice: Optional[str] = None) -> str:
    """
    Convert text to speech using Grok Voice via OpenAI SDK.

    Uses the OpenAI-compatible Realtime API with Grok's base URL.
    Available voices: ara, eve, leo, rex, sal

    Based on dillera's reachy_mini_conversation_app (HuggingFace).
    """
    import tempfile
    import asyncio

    # Voice priority: parameter > env var > default
    if voice and voice.lower() in GROK_VOICES:
        voice = voice.lower()
    else:
        voice = os.environ.get("GROK_VOICE", "eve").lower()

    async def _get_audio():
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1"
        )

        audio_chunks = []

        async with client.realtime.connect(model="grok-beta") as conn:
            # Configure session for TTS
            # Grok uses top-level "voice", OpenAI uses "audio.output.voice"
            await conn.session.update(
                session={
                    "modalities": ["audio", "text"],
                    "voice": voice,  # Grok's format
                    "instructions": "Repeat exactly what the user says. Nothing more.",
                    "turn_detection": None,
                }
            )

            # Send text to speak
            await conn.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"Say exactly: {text}"}]
                }
            )

            # Request response (no tools)
            await conn.response.create(response={"tool_choice": "none"})

            # Collect audio chunks
            async for event in conn:
                if event.type == "response.output_audio.delta":
                    audio_chunks.append(event.delta)
                elif event.type in ("response.output_audio.done", "response.done"):
                    break
                elif event.type == "error":
                    raise RuntimeError(f"Grok error: {event}")

        return audio_chunks

    # Run async function (handle case where event loop may already exist)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Already in async context - use nest_asyncio or create task
        import nest_asyncio
        nest_asyncio.apply()
        audio_chunks = asyncio.run(_get_audio())
    else:
        audio_chunks = asyncio.run(_get_audio())

    if not audio_chunks:
        raise RuntimeError("No audio received from Grok")

    # Decode base64 audio chunks and combine
    pcm_data = b"".join(base64.b64decode(chunk) for chunk in audio_chunks)

    # Convert PCM to WAV (24kHz 16-bit mono)
    import wave
    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(temp_file.name, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm_data)

    return temp_file.name


def speech_to_text(audio_data: bytes) -> str:
    """
    Convert audio to text using Deepgram STT (Nova-2).

    Args:
        audio_data: Raw audio bytes (WAV format expected from robot)

    Returns:
        Transcribed text
    """
    import httpx

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY environment variable not set")

    # Deepgram pre-recorded transcription endpoint
    url = "https://api.deepgram.com/v1/listen?model=nova-2&punctuate=true&smart_format=true"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/wav"
    }

    response = httpx.post(url, headers=headers, content=audio_data, timeout=30.0)
    response.raise_for_status()

    result = response.json()

    # Extract transcript from Deepgram response
    try:
        transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
        return transcript if transcript else ""
    except (KeyError, IndexError):
        return ""


def _parse_choreographed_text(text: str) -> list[dict]:
    """
    Parse text with embedded move markers.

    Syntax: "Hello [move:enthusiastic1] world [move:grateful1]"

    Returns list of segments:
        [{"type": "text", "content": "Hello "},
         {"type": "move", "name": "enthusiastic1"},
         {"type": "text", "content": " world "},
         {"type": "move", "name": "grateful1"}]
    """
    import re
    segments = []
    pattern = r'\[move:([^\]]+)\]'
    last_end = 0

    for match in re.finditer(pattern, text):
        # Text before the marker
        if match.start() > last_end:
            segments.append({"type": "text", "content": text[last_end:match.start()]})
        # The move marker
        segments.append({"type": "move", "name": match.group(1)})
        last_end = match.end()

    # Remaining text after last marker
    if last_end < len(text):
        segments.append({"type": "text", "content": text[last_end:]})

    return segments


@mcp.tool()
def speak(
    text: str,
    listen_after: float = 0,
    voice: Literal["ara", "eve", "leo", "rex", "sal"] = "eve"
) -> str:
    """
    Speak through the robot's speaker.

    Uses text-to-speech to vocalize. Supports embedded move markers
    for choreographed performances where speech and motion happen together.

    Syntax for embedded moves:
        "This is amazing [move:enthusiastic1] Jack, wonderful idea [move:grateful1]"

    Moves play concurrently with speech (non-blocking).
    Use list_moves() to see available move names.

    Args:
        text: What to say, optionally with [move:name] markers
        listen_after: Seconds to listen after speaking (0 = don't listen)
        voice: Grok voice - ara (warm), eve (energetic), leo (authoritative), rex (confident), sal (neutral)

    Returns:
        Confirmation, plus transcription if listen_after > 0
    """
    import time
    result_parts = []

    def _say(content: str) -> float:
        """TTS, upload, play, return estimated duration in seconds."""
        audio_path = text_to_speech(content, voice)
        try:
            _upload_and_play(audio_path)
        finally:
            os.unlink(audio_path)
        return _estimate_speech_duration(content)

    try:
        # Check if it's a file path (no choreography support for raw audio)
        if text.endswith(('.wav', '.mp3', '.ogg')):
            _upload_and_play(text)
            result_parts.append(f"Played audio: {text}")

        # Check for embedded moves
        elif '[move:' in text:
            segments = _parse_choreographed_text(text)
            moves_triggered = []
            speech_parts = []
            pending_move = None

            for segment in segments:
                if segment["type"] == "move":
                    # Queue the move to fire before the next speech chunk
                    pending_move = segment["name"]
                elif segment["type"] == "text":
                    content = segment["content"].strip()
                    if content:
                        # Fire pending move and wait for it to complete
                        if pending_move:
                            _do_move(pending_move)
                            _wait_for_moves_complete(timeout=10.0)
                            moves_triggered.append(pending_move)
                            pending_move = None

                        # Speak this chunk; block for estimated duration so the
                        # next chunk doesn't trample this one's playback.
                        time.sleep(_say(content))
                        speech_parts.append(content)

            # Fire any trailing move (if text ends with a move marker)
            if pending_move:
                _do_move(pending_move)
                moves_triggered.append(pending_move)

            result_parts.append(f"Performed: '{' '.join(speech_parts)}' with moves: {moves_triggered}")

        else:
            # Simple speech - no choreography
            time.sleep(_say(text))
            result_parts.append(f"Spoke: {text}")

        # Listen after speaking if requested
        if listen_after > 0:
            import time
            # Wait for audio playback to complete before listening
            # This prevents the mic from picking up the robot's own voice
            _wait_for_moves_complete(timeout=30.0)
            time.sleep(0.5)  # Buffer for audio pipeline latency

            transcript = _do_listen(listen_after)
            if transcript:
                result_parts.append(f"Heard: {transcript}")
            else:
                result_parts.append("Heard: (silence or unclear audio)")

        return " | ".join(result_parts)

    except Exception as e:
        return f"Speech failed: {e}"


def _do_listen(duration: float) -> str:
    """Internal helper - capture and transcribe audio."""
    import time
    import io
    import wave
    import numpy as np

    duration = max(1, min(30, duration))
    robot = get_robot()

    # Record with proper cleanup
    robot.media.start_recording()
    try:
        time.sleep(duration)
        audio_data = robot.media.get_audio_sample()
    finally:
        robot.media.stop_recording()

    if audio_data is not None and len(audio_data) > 0:
        # Convert numpy array to WAV bytes for Deepgram
        sample_rate = robot.media.get_input_audio_samplerate()
        channels = robot.media.get_input_channels()

        # Create WAV file in memory
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(channels if channels > 0 else 1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate if sample_rate > 0 else 16000)

            # Convert float32 to int16
            if isinstance(audio_data, np.ndarray):
                if audio_data.dtype == np.float32:
                    audio_int16 = (audio_data * 32767).astype(np.int16)
                else:
                    audio_int16 = audio_data.astype(np.int16)
                wav_file.writeframes(audio_int16.tobytes())
            else:
                wav_file.writeframes(audio_data)

        wav_bytes = wav_buffer.getvalue()

        # Transcribe via Deepgram STT
        transcript = speech_to_text(wav_bytes)
        return transcript if transcript else ""
    else:
        return ""


@mcp.tool()
def listen(duration: float = 3.0) -> str:
    """
    Listen through the robot's microphones and transcribe.

    Captures audio for the specified duration and converts to text
    using Deepgram Nova-2 speech-to-text.

    Args:
        duration: How long to listen in seconds (1-30)

    Returns:
        Transcribed text of what was heard
    """
    try:
        transcript = _do_listen(duration)
        if transcript:
            return f"Heard: {transcript}"
        else:
            return "Heard: (silence or unclear audio)"
    except Exception as e:
        return f"Listen failed: {e}"


@mcp.tool()
def snap() -> str:
    """
    Capture an image from the robot's camera.

    Returns the current view as base64-encoded image.
    Use this to perceive the environment.

    Returns:
        Base64-encoded image data (JPEG)
    """
    robot = get_robot()

    try:
        frame = robot.media.get_frame()

        if frame is not None:
            import cv2
            _, buffer = cv2.imencode('.jpg', frame)
            encoded = base64.b64encode(buffer).decode('utf-8')
            return f"data:image/jpeg;base64,{encoded}"
        else:
            return "No frame captured"

    except ImportError:
        return "OpenCV not available for image encoding"
    except Exception as e:
        return f"Vision failed: {e}"


@mcp.tool()
def rest(mode: Literal["neutral", "sleep", "wake"] = "neutral") -> str:
    """
    Control robot rest state.

    Args:
        mode:
            - "neutral": Return to neutral pose (default)
            - "sleep": Enter sleep mode (low power)
            - "wake": Wake from sleep mode

    Returns:
        Confirmation
    """
    try:
        if mode == "sleep":
            _daemon_post("/move/play/goto_sleep")
            return "Robot sleeping"
        elif mode == "wake":
            _daemon_post("/move/play/wake_up")
            return "Robot awakened"
        else:  # neutral
            return _do_express("neutral")
    except Exception as e:
        return f"Rest failed: {e}"


# ==============================================================================
# RECORDED MOVES (Pollen's emotion/dance libraries)
# ==============================================================================


def _wait_for_moves_complete(timeout: float = 30.0, poll_interval: float = 0.1) -> bool:
    """
    Wait for all moves to complete by polling the daemon.

    Returns True if moves completed, False if timeout.
    """
    import time
    import httpx

    start = time.time()
    while time.time() - start < timeout:
        try:
            response = httpx.get(f"{DAEMON_URL}/move/running", timeout=2.0)
            if response.status_code == 200:
                running = response.json()
                if not running:  # Empty list = all moves done
                    return True
        except (httpx.RequestError, httpx.TimeoutException):
            pass  # Connection error, keep polling
        time.sleep(poll_interval)
    return False


MOVE_LIBRARIES = {
    "emotions": "pollen-robotics/reachy-mini-emotions-library",
    "dances": "pollen-robotics/reachy-mini-dances-library",
}


@mcp.tool()
def discover(library: Literal["emotions", "dances"] = "emotions") -> str:
    """
    Discover available moves from Pollen's HuggingFace libraries.

    Returns move names that can be passed to show(move=...).
    Moves are professionally choreographed by Pollen Robotics.

    Args:
        library: Which library - "emotions" (81 expressions) or "dances"

    Returns:
        Available move names
    """
    import httpx

    dataset = MOVE_LIBRARIES.get(library)
    if not dataset:
        return f"Unknown library: {library}. Available: {list(MOVE_LIBRARIES.keys())}"

    try:
        response = httpx.get(
            f"{DAEMON_URL}/move/recorded-move-datasets/list/{dataset}",
            timeout=10.0
        )
        response.raise_for_status()
        moves = response.json()
        return f"Available {library} ({len(moves)}): {', '.join(sorted(moves))}"
    except httpx.ConnectError:
        return "Cannot connect to daemon. Is it running at $REACHY_DAEMON_URL?"
    except Exception as e:
        return f"Failed to list moves: {e}"


def _do_play_move(move_name: str, library: str = "emotions") -> str:
    """Internal helper - play a recorded move."""
    import httpx

    dataset = MOVE_LIBRARIES.get(library)
    if not dataset:
        return f"Unknown library: {library}. Available: {list(MOVE_LIBRARIES.keys())}"

    try:
        response = httpx.post(
            f"{DAEMON_URL}/move/play/recorded-move-dataset/{dataset}/{move_name}",
            timeout=30.0
        )
        if response.status_code == 404:
            return f"Move '{move_name}' not found in {library}. Use discover() to see available options."
        response.raise_for_status()
        result = response.json()
        return f"Playing: {move_name} (uuid: {result.get('uuid', 'unknown')})"
    except httpx.ConnectError:
        return "Cannot connect to daemon. Is it running at $REACHY_DAEMON_URL?"
    except Exception as e:
        return f"Failed to play move: {e}"


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    """Run the MCP server."""
    import atexit
    atexit.register(cleanup_robot)
    mcp.run()


if __name__ == "__main__":
    main()
