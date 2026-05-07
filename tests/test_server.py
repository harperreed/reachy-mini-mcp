# ABOUTME: Integration tests for src.server — exercises the 6 MCP tools with mocked deps.
# ABOUTME: robot.* and voice.* are mocked at module boundaries; logic + error paths are real.

from __future__ import annotations

import numpy as np
import pytest

from src import expressions, robot, server, voice


@pytest.fixture
def fake_robot(mocker):
    """Mock all of src.robot used by server.py at the call boundary."""
    mocker.patch("src.server.robot.goto")
    mocker.patch("src.server.robot.play_move", return_value={"uuid": "abc-123"})
    mocker.patch("src.server.robot.list_moves", return_value=["joy1", "fear1"])
    mocker.patch("src.server.robot.wake")
    mocker.patch("src.server.robot.sleep_pose")
    mocker.patch("src.server.robot.wait_for_moves", return_value=True)
    mocker.patch("src.server.robot.get_frame_jpeg", return_value=b"\xff\xd8\xff\xd9")
    mocker.patch("src.server.robot.output_sample_rate", return_value=48000)
    mocker.patch("src.server.robot.play_pcm")
    return robot


@pytest.fixture
def fake_voice(mocker):
    mocker.patch("src.server.voice.tts", return_value=b"mp3-bytes")
    mocker.patch("src.server.voice.mp3_to_pcm", return_value=np.zeros(48000, dtype=np.float32))
    return voice


def test_show_emotion_calls_goto_with_expression_data(fake_robot):
    out = server.show(emotion="joy")
    expected = expressions.EXPRESSIONS["joy"]
    fake_robot.goto.assert_called_once()
    kwargs = fake_robot.goto.call_args.kwargs
    assert kwargs["pitch"] == expected["head"]["pitch"]
    assert kwargs["antennas"] == (expected["antennas"][0], expected["antennas"][1])
    assert kwargs["duration"] == expected["duration"]
    assert "expressed: joy" in out


def test_show_unknown_emotion_returns_error_string(fake_robot):
    out = server.show(emotion="bogus")
    assert "unknown emotion" in out
    fake_robot.goto.assert_not_called()


def test_show_with_move_calls_play_move(fake_robot):
    out = server.show(emotion="neutral", move="loving1")
    fake_robot.play_move.assert_called_once_with("loving1", library="emotions")
    fake_robot.goto.assert_not_called()
    assert "playing: loving1" in out


def test_show_robot_error_returns_string(fake_robot):
    fake_robot.goto.side_effect = robot.DaemonError("daemon offline")
    out = server.show(emotion="joy")
    assert "expression failed" in out
    assert "daemon offline" in out


def test_look_clamps_out_of_range_inputs(fake_robot):
    server.look(roll=999, pitch=-999, yaw=9999, z=99, duration=99)
    kwargs = fake_robot.goto.call_args.kwargs
    assert kwargs["roll"] == 40.0
    assert kwargs["pitch"] == -40.0
    assert kwargs["yaw"] == 180.0
    assert kwargs["z"] == 20.0
    assert kwargs["duration"] == 5.0


def test_rest_sleep_calls_sleep_pose(fake_robot):
    out = server.rest(mode="sleep")
    fake_robot.sleep_pose.assert_called_once()
    assert "sleeping" in out


def test_rest_wake_calls_wake(fake_robot):
    out = server.rest(mode="wake")
    fake_robot.wake.assert_called_once()
    assert "awakened" in out


def test_rest_neutral_calls_goto(fake_robot):
    server.rest(mode="neutral")
    fake_robot.goto.assert_called_once()


def test_discover_returns_sorted_moves(fake_robot):
    out = server.discover(library="emotions")
    fake_robot.list_moves.assert_called_once_with("emotions")
    # Sorted alphabetically: fear1, joy1
    assert "fear1, joy1" in out


def test_snap_returns_base64_data_uri(fake_robot):
    out = server.snap()
    assert out.startswith("data:image/jpeg;base64,")
    # The fake JPEG bytes should round-trip through base64.
    import base64
    encoded = out.split(",", 1)[1]
    assert base64.b64decode(encoded) == b"\xff\xd8\xff\xd9"


def test_snap_propagates_media_error_as_string(fake_robot):
    fake_robot.get_frame_jpeg.side_effect = robot.MediaError("camera offline")
    out = server.snap()
    assert "capture failed" in out
    assert "camera offline" in out


def test_speak_simple_calls_tts_and_play_pcm(fake_robot, fake_voice):
    out = server.speak(text="hello world")
    fake_voice.tts.assert_called_once()
    assert fake_voice.tts.call_args.args[0] == "hello world"
    fake_robot.play_pcm.assert_called_once()
    fake_robot.play_move.assert_not_called()
    assert "spoke: hello world" in out


def test_speak_choreography_orders_moves_and_speech(fake_robot, fake_voice, mocker):
    """
    Verify: text-before-move plays first, then the move fires, then text-after-move.
    Sequence: speak("Hi [move:joy] there") →
        tts("Hi") → play_pcm → play_move(joy) → wait_for_moves → tts("there") → play_pcm
    """
    call_log = []
    fake_voice.tts.side_effect = lambda text, **kw: (call_log.append(("tts", text)), b"x")[1]
    fake_robot.play_pcm.side_effect = lambda *a, **kw: call_log.append(("play_pcm",))
    fake_robot.play_move.side_effect = lambda name, **kw: call_log.append(("play_move", name)) or {"uuid": "x"}
    fake_robot.wait_for_moves.side_effect = lambda **kw: call_log.append(("wait",)) or True

    out = server.speak(text="Hi [move:joy] there")

    assert call_log == [
        ("tts", "Hi"),
        ("play_pcm",),
        ("play_move", "joy"),
        ("wait",),
        ("tts", "there"),
        ("play_pcm",),
    ]
    assert "with moves: ['joy']" in out


def test_speak_voice_error_returns_string(fake_robot, fake_voice):
    fake_voice.tts.side_effect = voice.VoiceError("no api key")
    out = server.speak(text="hello")
    assert "speech failed" in out
    assert "no api key" in out


def test_speak_explicit_voice_id_passed_through(fake_robot, fake_voice):
    server.speak(text="hi", voice_id="custom-voice")
    assert fake_voice.tts.call_args.kwargs["voice_id"] == "custom-voice"


def test_speak_trailing_move_fires_after_last_text(fake_robot, fake_voice):
    out = server.speak(text="see ya [move:agreeing]")
    fake_robot.play_move.assert_called_once_with("agreeing")
    assert "agreeing" in out


def test_mcp_tools_registered():
    """Sanity check: the FastMCP instance has all six tools registered."""
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names == ["discover", "look", "rest", "show", "snap", "speak"]
