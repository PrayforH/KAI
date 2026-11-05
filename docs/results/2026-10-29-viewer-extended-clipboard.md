# Real-browser desktop reconnect repair

## Reproduction and cause

79 ran viewer v11. The authenticated Chrome assistant page remained disconnected.
Clicking reconnect opened its WebSocket, then the viewer logged:

`desktop session ended by to_desktop: ValueError: desktop client message length is out of range`

The actual noVNC client answers x11vnc's ExtendedClipboard capability declaration
even with `viewOnly` enabled. Its ClientCutText header encodes payload length as a
negative signed 32-bit integer. The filter read this as unsigned; a valid -8 became
4,294,967,288 and triggered the size guard. Earlier simplified handshakes omitted
this real client behavior and did not establish browser compatibility.

The checked-in package's `core/rfb.js` (`extendedClipboardCaps`, `clientCutText`,
`_handleServerCutText`) confirms the wire representation.

## Change

Parse signed clipboard lengths and bound their absolute span. Pass structurally
valid capability negotiation in observe mode. Keep clipboard content behind the
existing confirmed-human control gate. Preserve message alignment across partial
frames and following requests; reject invalid or oversized extensions.

## Verification

- Viewer suite: 19 passed; ruff and diff whitespace checks passed.
- Regression includes the exact noVNC Caps packet, fragmented header/payload,
  continued framebuffer request, clipboard content gating, and signed length limits.
- Deployed only `computer-viewer` as `kai/computer-viewer:20261029-v12-clipboard`.
  Image ID: `sha256:f0ad4cfb104632b34e079363abfc4e2a234801896aeda0aa573a5556728faef5`.
- Native Chrome, existing owner session at
  `https://172.20.105.79:3543/assistant`: clicked reconnect; visually confirmed the
  actual remote browser and terminal rendered, with `我的电脑 已连接 观察中`.
- Reloaded the whole assistant page, then again visually confirmed the desktop
  rendered and the connected status persisted beyond the original immediate close.
- Viewer logs show the first connection ending on page reload and a new open
  connection, with no repeated clipboard-length exception.
- VM was not restarted (`StartedAt=2026-10-28T02:05:18.809344965Z`). Existing
  desktop windows, files, account configuration and history were preserved.

This verifies the reported browser connection and reload failure. It does not
claim the remaining broader Dot implementation scope is complete.

## Rollback

The previous private compose file is retained on 79 at
`/data/kai-79-20261013/compose.private.before-signed-clipboard.json`.
Restore the viewer image to v11 and recreate only that service if rollback is
needed. The backup contains private configuration and must not be published.
