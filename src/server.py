# ABOUTME: MCP server entry point — defines six tools that wrap robot + voice modules.
# ABOUTME: Tool functions stay thin: catch typed errors, return readable strings to the host.

from __future__ import annotations

import base64
from typing import Literal

from mcp.server.fastmcp import FastMCP

from . import robot, voice
from .expressions import EXPRESSIONS, parse_choreographed_text

mcp = FastMCP(
    name="reachy-mini",
    instructions="""
    Reachy Mini robot control for expressive robotics.

    Six tools:
      - show(emotion=..., move=...)   12 built-in emotions or 80+ recorded moves
      - look(roll, pitch, yaw, z)     direct head positioning in degrees
      - speak(text)                   ElevenLabs TTS via robot speaker
                                      supports [move:name] choreography markers
      - snap()                        camera frame as base64 JPEG
      - rest(mode)                    neutral / sleep / wake
      - discover(library)             list available recorded moves

    Prefer show() for common emotions; show(move=...) for nuanced expressions
    from Pollen's library. discover() reveals what's available.
    """,
)


# --------------------------------------------------------------------------- #
# show / look / rest / discover                                               #
# --------------------------------------------------------------------------- #


def _do_show_emotion(emotion: str) -> str:
    expr = EXPRESSIONS.get(emotion)
    if expr is None:
        return f"unknown emotion {emotion!r}; available: {sorted(EXPRESSIONS)}"
    head = expr["head"]
    antennas = expr["antennas"]
    try:
        robot.goto(
            z=head["z"],
            roll=head["roll"],
            pitch=head["pitch"],
            yaw=head["yaw"],
            antennas=(antennas[0], antennas[1]),
            duration=expr["duration"],
            interpolation=expr["method"],
        )
        return f"expressed: {emotion}"
    except robot.RobotError as e:
        return f"expression failed: {e}"


def _do_play_move(name: str, library: str = "emotions") -> str:
    try:
        result = robot.play_move(name, library=library)
        uuid = result.get("uuid", "unknown") if isinstance(result, dict) else "unknown"
        return f"playing: {name} (uuid: {uuid})"
    except robot.RobotError as e:
        return f"move failed: {e}"


@mcp.tool()
def show(
    emotion: Literal[
        "neutral", "curious", "uncertain", "recognition", "joy",
        "thinking", "listening", "agreeing", "disagreeing",
        "sleepy", "surprised", "focused",
    ] = "neutral",
    move: str = "",
) -> str:
    """Express an emotion through movement.

    Use ``emotion`` for 12 fast built-in expressions, or ``move`` for one of the
    80+ recorded moves from Pollen's HuggingFace library. ``move`` overrides
    ``emotion`` if both are passed. Use ``discover()`` to list available moves.
    """
    if move:
        return _do_play_move(move)
    return _do_show_emotion(emotion)


@mcp.tool()
def look(
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
    z: float = 0.0,
    duration: float = 1.0,
) -> str:
    """Direct head positioning in degrees.

    Ranges: roll/pitch ±40°, yaw ±180°, z ±20 (internal units), duration 0.1–5.0 s.
    Values outside the safe range are clamped.
    """
    roll = max(-40.0, min(40.0, roll))
    pitch = max(-40.0, min(40.0, pitch))
    yaw = max(-180.0, min(180.0, yaw))
    z = max(-20.0, min(20.0, z))
    duration = max(0.1, min(5.0, duration))
    try:
        robot.goto(z=z, roll=roll, pitch=pitch, yaw=yaw, duration=duration)
        return f"head positioned: roll={roll}°, pitch={pitch}°, yaw={yaw}°, z={z}"
    except robot.RobotError as e:
        return f"movement failed: {e}"


@mcp.tool()
def rest(mode: Literal["neutral", "sleep", "wake"] = "neutral") -> str:
    """Control robot rest state."""
    try:
        if mode == "sleep":
            robot.sleep_pose()
            return "robot sleeping"
        if mode == "wake":
            robot.wake()
            return "robot awakened"
        return _do_show_emotion("neutral")
    except robot.RobotError as e:
        return f"rest failed: {e}"


@mcp.tool()
def discover(library: Literal["emotions", "dances"] = "emotions") -> str:
    """List recorded moves available for show(move=...)."""
    try:
        moves = robot.list_moves(library)
        return f"available {library} ({len(moves)}): {', '.join(sorted(moves))}"
    except robot.RobotError as e:
        return f"discover failed: {e}"


# --------------------------------------------------------------------------- #
# snap                                                                        #
# --------------------------------------------------------------------------- #


@mcp.tool()
def snap() -> str:
    """Capture an image from the robot's camera (base64 JPEG data URI)."""
    try:
        jpeg = robot.get_frame_jpeg()
        encoded = base64.b64encode(jpeg).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except robot.RobotError as e:
        return f"capture failed: {e}"


# --------------------------------------------------------------------------- #
# speak                                                                       #
# --------------------------------------------------------------------------- #


def _say(text: str, voice_id: str | None) -> None:
    """TTS one chunk of text and play through the robot speaker."""
    mp3 = voice.tts(text, voice_id=voice_id)
    rate = robot.output_sample_rate()
    pcm = voice.mp3_to_pcm(mp3, sample_rate=rate)
    robot.play_pcm(pcm, sample_rate=rate)


@mcp.tool()
def speak(text: str, voice_id: str = "") -> str:
    """Speak through the robot's speaker via ElevenLabs.

    Supports embedded ``[move:name]`` markers for choreographed performances:
        speak("This is amazing [move:joy] wonderful idea [move:agreeing]")
    Moves fire before each text chunk and complete before speech resumes.

    If ``voice_id`` is empty, falls back to ELEVENLABS_VOICE_ID env var, then
    a default voice. Pass any ElevenLabs voice ID to override.
    """
    voice_arg = voice_id or None
    try:
        if "[move:" in text:
            segments = parse_choreographed_text(text)
            spoken: list[str] = []
            moves: list[str] = []
            pending_move: str | None = None
            for segment in segments:
                if segment["type"] == "move":
                    pending_move = segment["name"]
                elif segment["type"] == "text":
                    chunk = segment["content"].strip()
                    if not chunk:
                        continue
                    if pending_move:
                        robot.play_move(pending_move)
                        robot.wait_for_moves(timeout=10.0)
                        moves.append(pending_move)
                        pending_move = None
                    _say(chunk, voice_arg)
                    spoken.append(chunk)
            if pending_move:
                robot.play_move(pending_move)
                moves.append(pending_move)
            return f"performed: {' '.join(spoken)!r} with moves: {moves}"
        _say(text, voice_arg)
        return f"spoke: {text}"
    except (robot.RobotError, voice.VoiceError) as e:
        return f"speech failed: {e}"


# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
