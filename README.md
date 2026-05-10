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

System dependencies first (macOS):

```bash
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
             gst-plugins-ugly gst-libav gobject-introspection \
             ffmpeg pkg-config git-lfs
git lfs install
```

GStreamer's WebRTC plugin ships inside `gst-plugins-bad`. Then install the
PyGObject binding into your venv (the `reachy-mini` package only pins it on
Linux, so macOS has to add it explicitly):

```bash
uv add pygobject
```

Then pick a flow:

### Run via uvx (no clone)

```bash
ELEVENLABS_API_KEY=your_key_here \
  uvx --from git+https://github.com/harperreed/reachy-mini-mcp reachy-mini-mcp
```

uvx fetches the repo, builds the package into a throwaway venv, and starts the stdio server. Best for hosts (Claude Desktop, Claude Code) that pass env vars via their MCP config block.

### Run from a local clone

```bash
git clone https://github.com/harperreed/reachy-mini-mcp
cd reachy-mini-mcp
uv sync
echo 'ELEVENLABS_API_KEY=your_key_here' > .env
uv run reachy-mini-mcp
```

`main()` calls `load_dotenv()` at startup, so any `.env` in the working directory is auto-loaded. The clone path is the right one when you want `.env` files, the test suite, or to hack on the code.

The daemon must be reachable either way. Default URL is `http://reachy-mini.local:8000/api`. Override with `REACHY_DAEMON_URL` for a local simulator or a different host.

## Architecture

```
Laptop (macOS)                       Robot (Pi CM4)
  Claude / ChatGPT / etc                reachy_mini daemon
       │ stdio                            :8000 REST  (motors, moves, state)
  reachy-mini-mcp                         :8443 WebRTC signaling
    ├─ httpx → daemon REST ────────►      libcamera → frames
    ├─ WebRTC client (frames in)          webrtcsink → speaker
    ├─ WebRTC client (PCM out) ────►
    ├─ ElevenLabs HTTP (MP3 in)
    └─ pydub/ffmpeg (MP3 → PCM)
```

Motors and recorded moves go over the daemon's REST API. Camera frames and speaker audio ride WebRTC, signalled directly to the robot host on `:8443` — the SDK's Zenoh layer is bypassed because it requires multicast scouting that most managed networks block. ElevenLabs returns MP3, which is decoded to mono float32 at the SDK's runtime sample rate and pushed via `media.push_audio_sample()`.

## MCP Config

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`.

uvx (no clone):

```json
{
  "mcpServers": {
    "reachy-mini": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/harperreed/reachy-mini-mcp",
        "reachy-mini-mcp"
      ],
      "env": { "ELEVENLABS_API_KEY": "your_key_here" }
    }
  }
}
```

Local clone:

```json
{
  "mcpServers": {
    "reachy-mini": {
      "command": "uv",
      "args": ["--directory", "/path/to/reachy-mini-mcp", "run", "reachy-mini-mcp"],
      "env": { "ELEVENLABS_API_KEY": "your_key_here" }
    }
  }
}
```

### Claude Code

uvx (no clone):

```bash
claude mcp add reachy-mini \
  --env ELEVENLABS_API_KEY=your_key_here \
  -- uvx --from git+https://github.com/harperreed/reachy-mini-mcp reachy-mini-mcp
```

Local clone:

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
| `REACHY_DAEMON_URL` | No | `http://reachy-mini.local:8000/api` | Daemon REST base URL |
| `REACHY_ROBOT_HOST` | No | hostname from `REACHY_DAEMON_URL` | WebRTC signalling host (port 8443). Set this if the robot's REST and media live on different hostnames. |

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

- **`gst-inspect-1.0 webrtcbin` returns nothing** — the WebRTC element didn't load. Re-run the brew line above; the WebRTC element ships in `gst-plugins-bad`.
- **`ModuleNotFoundError: No module named 'gi'`** — PyGObject isn't in the venv. Run `uv add pygobject` after the brew step. If the build fails, make sure `gobject-introspection` and `pkg-config` are installed via brew and that `which pkg-config` resolves to `/opt/homebrew/bin/pkg-config`.
- **`uv sync` fails on the `reachy-mini` git dependency** — install `git-lfs` and run `git lfs install` once. The reachy-mini repo stores recorded moves under LFS.
- **`snap()` or `speak()` fails with "failed to start WebRTC media"** — the robot host the server is dialing isn't reachable on `:8443`. By default that host is the hostname from `REACHY_DAEMON_URL`; set `REACHY_ROBOT_HOST=<robot-ip>` if the media plane lives elsewhere, then restart.
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
