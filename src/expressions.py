# ABOUTME: Built-in emotion choreography table and embedded-move text parser.
# ABOUTME: Pure data + string parsing — no I/O, no SDK calls, trivially testable.

from __future__ import annotations

import re
from typing import TypedDict


class HeadPose(TypedDict):
    z: float
    roll: float
    pitch: float
    yaw: float


class Expression(TypedDict):
    head: HeadPose
    antennas: list[int]
    duration: float
    method: str


# Built-in emotional states → motor choreography.
# head: degrees for roll/pitch/yaw, internal units for z (passed through to daemon).
# antennas: [left, right] in degrees.
EXPRESSIONS: dict[str, Expression] = {
    "neutral": {
        "head": {"z": 0, "roll": 0, "pitch": 0, "yaw": 0},
        "antennas": [0, 0],
        "duration": 1.5,
        "method": "minjerk",
    },
    "curious": {
        "head": {"z": 0, "roll": 0, "pitch": 10, "yaw": 8},
        "antennas": [20, 20],
        "duration": 1.2,
        "method": "ease_in_out",
    },
    "uncertain": {
        "head": {"z": 0, "roll": 8, "pitch": -3, "yaw": 3},
        "antennas": [-15, 15],
        "duration": 2.0,
        "method": "minjerk",
    },
    "recognition": {
        "head": {"z": 0, "roll": 0, "pitch": 5, "yaw": 0},
        "antennas": [30, 30],
        "duration": 0.8,
        "method": "cartoon",
    },
    "joy": {
        "head": {"z": 0, "roll": -3, "pitch": 8, "yaw": 0},
        "antennas": [40, 40],
        "duration": 1.0,
        "method": "cartoon",
    },
    "thinking": {
        "head": {"z": 0, "roll": 5, "pitch": 3, "yaw": 12},
        "antennas": [8, -8],
        "duration": 1.5,
        "method": "ease_in_out",
    },
    "listening": {
        "head": {"z": 0, "roll": -3, "pitch": 8, "yaw": 0},
        "antennas": [25, 25],
        "duration": 1.0,
        "method": "minjerk",
    },
    "agreeing": {
        "head": {"z": 0, "roll": 0, "pitch": 8, "yaw": 0},
        "antennas": [20, 20],
        "duration": 0.5,
        "method": "ease_in_out",
    },
    "disagreeing": {
        "head": {"z": 0, "roll": 0, "pitch": 0, "yaw": 12},
        "antennas": [-8, -8],
        "duration": 0.4,
        "method": "ease_in_out",
    },
    "sleepy": {
        "head": {"z": 0, "roll": 8, "pitch": -10, "yaw": 0},
        "antennas": [-20, -20],
        "duration": 2.5,
        "method": "minjerk",
    },
    "surprised": {
        "head": {"z": 0, "roll": 0, "pitch": -8, "yaw": 0},
        "antennas": [45, 45],
        "duration": 0.3,
        "method": "cartoon",
    },
    "focused": {
        "head": {"z": 0, "roll": 0, "pitch": 6, "yaw": 0},
        "antennas": [18, 18],
        "duration": 1.0,
        "method": "minjerk",
    },
}


_MOVE_MARKER = re.compile(r"\[move:([^\]]+)\]")


class TextSegment(TypedDict):
    type: str  # "text"
    content: str


class MoveSegment(TypedDict):
    type: str  # "move"
    name: str


def parse_choreographed_text(text: str) -> list[dict]:
    """Split a string with embedded [move:name] markers into ordered segments.

    Example:
        "Hello [move:joy] world" →
          [{"type": "text", "content": "Hello "},
           {"type": "move", "name": "joy"},
           {"type": "text", "content": " world"}]
    """
    segments: list[dict] = []
    last_end = 0
    for match in _MOVE_MARKER.finditer(text):
        if match.start() > last_end:
            segments.append({"type": "text", "content": text[last_end:match.start()]})
        segments.append({"type": "move", "name": match.group(1)})
        last_end = match.end()
    if last_end < len(text):
        segments.append({"type": "text", "content": text[last_end:]})
    return segments
