# Reachy Mini MCP

**Give your AI a body.**

This MCP server lets AI systems control [Pollen Robotics' Reachy Mini](https://www.pollen-robotics.com/reachy-mini/) robot — speak, see, and express emotions through physical movement. Works with Claude, ChatGPT, or any MCP-compatible client.

6 tools. Real hardware or simulator. Built on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and [reachy-mini](https://github.com/pollen-robotics/reachy_mini) develop branch.

https://reachy-mini-mcp-969sxyq.gamma.site/

---

## For AI Systems

Token-efficient tool reference for programmatic use:

| Tool | Args | Purpose |
|------|------|---------|
| `speak` | `text, voice_id=""` | TTS via ElevenLabs, with optional `[move:name]` markers |
| `show` | `emotion, move=""` | Express built-in emotion or play a recorded move |
| `look` | `roll, pitch, yaw, z, duration` | Direct head pose (degrees) |
| `snap` | – | Camera capture (base64 JPEG) |
| `rest` | `mode="neutral"` | `neutral` / `sleep` / `wake` |
| `discover` | `library="emotions"` | List available recorded moves |

### speak()

Choreographed speech via embedded move markers:

```
speak("[move:curious1] What's this? [move:surprised1] Oh wow!")
```

Each move fires between speech segments and the server waits for it to complete before the next segment plays. Pass `voice_id="..."` to override the default ElevenLabs voice for one call.

### show()

Built-in emotions (fast, local pose):
`neutral`, `curious`, `uncertain`, `recognition`, `joy`, `thinking`, `listening`, `agreeing`, `disagreeing`, `sleepy`, `surprised`, `focused`

Recorded moves (~80 shipped by Pollen):

```
show(move="loving1")
show(move="fear1")
show(move="serenity1")
```

Use `discover()` to enumerate them.

---

## Quick Start

```bash
# 1. System dependencies (macOS)
brew install gstreamer gst-plugins-good gst-plugins-bad gst-plugins-ugly \
             gst-libav gst-plugin-webrtc ffmpeg pkg-config git-lfs
git lfs install

# 2. Install
cd reachy-mini-mcp
uv sync

# 3. ElevenLabs key (required for speak)
export ELEVENLABS_API_KEY=your_key_here

# 4. Run the MCP server (stdio)
uv run reachy-mini-mcp
```

The daemon must be reachable. Default URL is `http://reachy-mini.local:8321/api`. Override with `REACHY_DAEMON_URL` for a local simulator or a different host.

## Architecture

```
Laptop (macOS)                       Robot (Pi CM4)
  Claude / ChatGPT / etc                reachy_mini daemon
       │ stdio                            :8321 REST  (motors, moves, state)
  reachy-mini-mcp                         :7447 Zenoh (motors, state pub/sub)
    ├─ Zenoh peer ─────────────────►      :8443 WebRTC signaling
    ├─ WebRTC client (frames in)
    ├─ WebRTC client (PCM out) ────►      libcamera → frames
    ├─ ElevenLabs HTTP (MP3 in)           webrtcsink → speaker
    └─ pydub/ffmpeg (MP3 → PCM)
```

Motors and state ride Zenoh. Camera frames and speaker audio ride WebRTC. ElevenLabs returns MP3, which is decoded to mono float32 at the SDK's runtime sample rate and pushed via `media.push_audio_sample()`.

## MCP Config

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "reachy-mini": {
      "command": "uv",
      "args": ["--directory", "/path/to/reachy-mini-mcp", "run", "reachy-mini-mcp"],
      "env": {
        "ELEVENLABS_API_KEY": "your_key_here"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add reachy-mini \
  --env ELEVENLABS_API_KEY=your_key_here \
  -- uv --directory /path/to/reachy-mini-mcp run reachy-mini-mcp
```

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ELEVENLABS_API_KEY` | Yes (for `speak`) | – | ElevenLabs TTS auth |
| `ELEVENLABS_VOICE_ID` | No | `JBFqnCBsd6RMkjVDRZzb` (Rachel) | Default voice |
| `ELEVENLABS_MODEL` | No | `eleven_flash_v2_5` | Model id |
| `REACHY_DAEMON_URL` | No | `http://reachy-mini.local:8321/api` | Daemon REST base URL |
| `REACHY_ZENOH_ENDPOINT` | No | – | Manual Zenoh endpoint when multicast is blocked, e.g. `tcp/192.168.1.42:7447` |

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- GStreamer + WebRTC plugins (for camera + speaker)
- ffmpeg (for MP3 decode)
- git-lfs (the `reachy-mini` install pulls LFS assets)

## Development

```bash
uv sync                 # install + dev deps
uv run pytest -q        # 43 tests, hits no real network or hardware
uv run ruff check src tests
```

`tests/` mocks the daemon (`pytest-httpx`) and the SDK singleton (`MagicMock`), so the suite runs fully offline. The MP3 fixture skips automatically if ffmpeg is not on the path.

## Troubleshooting

- **`gst-inspect-1.0 webrtc` returns nothing** — the WebRTC plugin pack didn't install. Re-run the brew line above; you need `gst-plugin-webrtc` plus the `bad` plugins.
- **`uv sync` fails on the `reachy-mini` git dependency** — install `git-lfs` and run `git lfs install` once. The reachy-mini repo stores recorded moves under LFS.
- **Robot can't be reached on Zenoh** — your network probably blocks multicast. Set `REACHY_ZENOH_ENDPOINT=tcp/<robot-ip>:7447` and restart the server.
- **`speak()` returns "speech failed: ELEVENLABS_API_KEY"** — set the env var in your MCP config, not just your shell. Claude Desktop launches the server with its own environment.
- **`snap()` returns "capture failed: no frame"** — the WebRTC video track hasn't started yet. Wait a few seconds after server boot and retry; the daemon negotiates the stream lazily.
- **Speech sounds pitch-shifted or robotic** — the SDK's output sample rate didn't match the PCM. The server queries `mini.media.get_output_audio_samplerate()` at decode time, so this should self-heal — but if you've pinned an older daemon, upgrade it.

## License

MIT License — see [LICENSE](LICENSE).

## Acknowledgments

- [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini) by [Pollen Robotics](https://www.pollen-robotics.com/) (Apache 2.0)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [ElevenLabs](https://elevenlabs.io/) for TTS

## Links

- [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini) (Apache 2.0)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [mVara](https://mvara.ai/)
