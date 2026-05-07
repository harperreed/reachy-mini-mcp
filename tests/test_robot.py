# ABOUTME: Unit tests for src.robot — daemon REST client + SDK media wrappers.
# ABOUTME: Network is mocked via pytest-httpx; the SDK singleton is replaced with a MagicMock.

from __future__ import annotations

import math

import httpx
import numpy as np
import pytest

from src import robot


def test_goto_converts_degrees_to_radians(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="POST",
        url=f"{daemon_url}/move/goto",
        json={"ok": True},
    )

    robot.goto(z=5, roll=10, pitch=-20, yaw=30, antennas=(15, -15), duration=2.0)

    request = httpx_mock.get_request()
    assert request is not None
    body = request.read().decode()
    import json
    payload = json.loads(body)
    assert payload["head_pose"]["z"] == 5
    assert payload["head_pose"]["roll"] == pytest.approx(math.radians(10))
    assert payload["head_pose"]["pitch"] == pytest.approx(math.radians(-20))
    assert payload["head_pose"]["yaw"] == pytest.approx(math.radians(30))
    assert payload["antennas"][0] == pytest.approx(math.radians(15))
    assert payload["antennas"][1] == pytest.approx(math.radians(-15))
    assert payload["duration"] == 2.0
    assert payload["interpolation"] == "minjerk"


def test_daemon_post_retries_then_succeeds(httpx_mock, daemon_url):
    httpx_mock.add_exception(httpx.ConnectError("first attempt fails"))
    httpx_mock.add_response(
        method="POST",
        url=f"{daemon_url}/move/goto",
        json={"ok": True},
    )

    # Should not raise — the second attempt succeeds.
    robot.goto()
    assert len(httpx_mock.get_requests()) == 2


def test_daemon_post_raises_after_persistent_connect_error(httpx_mock, daemon_url):
    httpx_mock.add_exception(httpx.ConnectError("nope"))
    httpx_mock.add_exception(httpx.ConnectError("still nope"))

    with pytest.raises(robot.DaemonError, match="cannot reach daemon"):
        robot.goto()


def test_daemon_post_raises_on_5xx(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="POST",
        url=f"{daemon_url}/move/goto",
        status_code=500,
        text="kaboom",
    )

    with pytest.raises(robot.DaemonError, match="500"):
        robot.goto()


def test_play_move_404_includes_library_name(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="POST",
        url=f"{daemon_url}/move/play/recorded-move-dataset/"
            f"pollen-robotics/reachy-mini-emotions-library/zzznope",
        status_code=404,
        text="not found",
    )

    with pytest.raises(robot.DaemonError, match="zzznope.*emotions"):
        robot.play_move("zzznope", library="emotions")


def test_play_move_unknown_library():
    with pytest.raises(robot.DaemonError, match="unknown library"):
        robot.play_move("foo", library="not-a-library")


def test_list_moves_returns_list(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="GET",
        url=f"{daemon_url}/move/recorded-move-datasets/list/"
            f"pollen-robotics/reachy-mini-emotions-library",
        json=["joy1", "fear1", "loving1"],
    )
    assert robot.list_moves("emotions") == ["joy1", "fear1", "loving1"]


def test_list_moves_rejects_non_list(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="GET",
        url=f"{daemon_url}/move/recorded-move-datasets/list/"
            f"pollen-robotics/reachy-mini-emotions-library",
        json={"unexpected": "shape"},
    )
    with pytest.raises(robot.DaemonError, match="unexpected"):
        robot.list_moves("emotions")


def test_wait_for_moves_returns_true_when_empty(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="GET",
        url=f"{daemon_url}/move/running",
        json=[],
    )
    assert robot.wait_for_moves(timeout=1.0, poll_interval=0.01) is True


def test_wait_for_moves_returns_false_on_timeout(httpx_mock, daemon_url):
    # Always returns running tasks — never drains.
    httpx_mock.add_response(
        method="GET",
        url=f"{daemon_url}/move/running",
        json=["task-uuid-1"],
        is_reusable=True,
    )
    assert robot.wait_for_moves(timeout=0.05, poll_interval=0.01) is False


def test_wake_and_sleep(httpx_mock, daemon_url):
    httpx_mock.add_response(
        method="POST", url=f"{daemon_url}/move/play/wake_up", json={"ok": True}
    )
    httpx_mock.add_response(
        method="POST", url=f"{daemon_url}/move/play/goto_sleep", json={"ok": True}
    )
    robot.wake()
    robot.sleep_pose()
    assert len(httpx_mock.get_requests()) == 2


def test_get_frame_jpeg_returns_jpeg_bytes(fake_mini):
    fake_mini.media.get_frame.return_value = np.full((8, 8, 3), 128, dtype=np.uint8)
    out = robot.get_frame_jpeg()
    assert out.startswith(b"\xff\xd8")  # JPEG SOI marker
    assert b"\xff\xd9" in out[-4:] or out.endswith(b"\xff\xd9")  # EOI


def test_get_frame_jpeg_raises_on_no_frame(fake_mini):
    fake_mini.media.get_frame.return_value = None
    with pytest.raises(robot.MediaError, match="no frame"):
        robot.get_frame_jpeg()


def test_output_sample_rate(fake_mini):
    fake_mini.media.get_output_audio_samplerate.return_value = 48000
    assert robot.output_sample_rate() == 48000


def test_output_sample_rate_invalid(fake_mini):
    fake_mini.media.get_output_audio_samplerate.return_value = -1
    with pytest.raises(robot.MediaError, match="invalid sample rate"):
        robot.output_sample_rate()


def test_play_pcm_chunks_and_sleeps(fake_mini, mocker):
    sleep = mocker.patch("src.robot.time.sleep")
    pcm = np.zeros(2500, dtype=np.float32)

    robot.play_pcm(pcm, sample_rate=48000)

    push_calls = fake_mini.media.push_audio_sample.call_args_list
    assert len(push_calls) == 3  # ceil(2500 / 960) = 3
    total = sum(len(c.args[0]) for c in push_calls)
    assert total == 2500
    sleep.assert_called_once()
    assert sleep.call_args.args[0] == pytest.approx(2500 / 48000)


def test_play_pcm_rejects_stereo(fake_mini):
    stereo = np.zeros((1000, 2), dtype=np.float32)
    with pytest.raises(robot.MediaError, match="mono"):
        robot.play_pcm(stereo, sample_rate=48000)


def test_play_pcm_coerces_dtype(fake_mini, mocker):
    mocker.patch("src.robot.time.sleep")
    pcm = np.zeros(500, dtype=np.float64)
    robot.play_pcm(pcm, sample_rate=48000)
    pushed = fake_mini.media.push_audio_sample.call_args.args[0]
    assert pushed.dtype == np.float32
