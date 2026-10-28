"""A minimal RFB 3.x client for one guest desktop.

The agent's eyes and hands ride the same authenticated SSH display gate as the
human viewer: pixels in, pointer and keyboard events out, nothing else. The
client speaks just enough of the protocol to fetch a full frame and inject
input; it never executes guest commands. VNC legacy authentication is
supported with a small, self-contained DES so a guest that keeps ``rfbauth``
still works; a guest offering the None security type needs no shared secret.
"""

import asyncio
import ctypes
import struct
from collections.abc import Mapping
from io import BytesIO
from typing import Protocol

from PIL import Image
from PIL.Image import Resampling

SERVER_OK = 0
SEC_NONE = 1
SEC_VNC_AUTH = 2
RAW_ENCODING = 0


class DesktopTransport(Protocol):
    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]: ...


# X11 keysyms for the named keys the model is allowed to send.
NAMED_KEYSYMS: Mapping[str, int] = {
    "enter": 0xFF0D,
    "return": 0xFF0D,
    "backspace": 0xFF08,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "delete": 0xFFFF,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "up": 0xFF52,
    "down": 0xFF53,
    "left": 0xFF51,
    "right": 0xFF54,
    "space": 0x0020,
    "ctrl": 0xFFE3,
    "alt": 0xFFE9,
    "shift": 0xFFE1,
    "meta": 0xFFEB,
}
MODIFIER_ALIASES = {"control": "ctrl", "cmd": "meta", "command": "meta", "option": "alt"}
BUTTON_MASKS = {"left": 1, "middle": 2, "right": 4, "wheel_up": 8, "wheel_down": 16}


def keysym_for(text: str) -> int | None:
    """Map one character or named key to its X keysym; Unicode rides 0x01000000."""

    lowered = text.strip().lower()
    lowered = MODIFIER_ALIASES.get(lowered, lowered)
    if lowered in NAMED_KEYSYMS:
        return NAMED_KEYSYMS[lowered]
    if len(text) == 1:
        code = ord(text)
        if 32 <= code < 256 and text.isascii():
            # Latin-1 printable keysyms equal their code point.
            return code
        return 0x01000000 + code
    return None


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """VNC legacy authentication: single DES-ECB with a bit-reversed key.

    The node already trusts the platform crypto library for TLS; VNC auth is
    the same fixed 8-byte-key block cipher, so it reuses libcrypto instead of
    carrying a hand-rolled cipher. Absent the symbol, the error is explicit.
    """

    if len(challenge) != 16 or len(challenge) % 8:
        raise ValueError("VNC authentication challenge must be 16 bytes")
    library = _libcrypto()
    if library is None:
        raise RuntimeError("desktop VNC authentication needs the system crypto library")
    material = password.encode("ascii", "ignore")[:8].ljust(8, b"\0")
    key = (ctypes.c_ubyte * 8)(
        *(int(f"{byte:08b}"[::-1], 2) for byte in material)
    )
    schedule = (ctypes.c_ubyte * 128)()
    library.DES_set_key_unchecked(key, schedule)
    out = (ctypes.c_ubyte * 16)()
    library.DES_ecb_encrypt(
        (ctypes.c_ubyte * 16).from_buffer_copy(challenge), out, schedule, 1
    )
    return bytes(out)


class _LibCrypto:
    """The process's own crypto library, loaded once and probed for DES."""

    def __init__(self) -> None:
        self._library: ctypes.CDLL | None = None

    def __call__(self) -> ctypes.CDLL | None:
        if self._library is not None:
            return self._library
        # Linux container names first; dlopen(None) is a last resort. macOS
        # lacks a loadable libcrypto here, which surfaces as an explicit error.
        for name in ("libcrypto.so.3", "libcrypto.so", None):
            try:
                library = ctypes.CDLL(name) if name else ctypes.CDLL(None)
                if not hasattr(library, "DES_set_key_unchecked") or not hasattr(
                    library, "DES_ecb_encrypt"
                ):
                    continue
                library.DES_set_key_unchecked.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_void_p,
                ]
                library.DES_ecb_encrypt.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_int,
                ]
            except (OSError, AttributeError):
                continue
            self._library = library
            return library
        return None


_libcrypto = _LibCrypto()
class RfbClient:
    """One desktop connection: handshake, frame fetch, pointer and keyboard."""

    def __init__(self, transport: DesktopTransport, *, password: str | None = None) -> None:
        self._transport, self._password = transport, password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.width = 0
        self.height = 0
        self._bytes_per_pixel = 4
        self._frame = bytearray()

    async def __aenter__(self) -> "RfbClient":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        reader, writer = await self._transport.open()
        self._reader, self._writer = reader, writer
        # A transport may have consumed the banner to fail fast on a dead
        # desktop; reading it twice would deadlock against the server, which is
        # waiting for our version reply.
        preloaded = getattr(self._transport, "banner", None)
        server = preloaded if isinstance(preloaded, bytes) else await self._expect(12)
        if not server.startswith(b"RFB 003."):
            raise RuntimeError("desktop did not confirm the RFB transport")
        await self._send(server)  # Echo the server's version; 3.3-3.8 all supported.
        await self._handshake_security(server)
        await self._send(struct.pack(">B", 1))  # shared session
        init = await self._expect(24)
        self.width, self.height = struct.unpack(">HH", init[:4])
        if not 1 <= self.width <= 8192 or not 1 <= self.height <= 8192:
            raise RuntimeError("desktop reported an impossible framebuffer size")
        # PIXEL_FORMAT carries bpp and depth as single bytes; the shifts and
        # maxima describe the server's channel order, which we keep.
        bpp = init[4]
        if bpp not in {16, 32}:
            raise RuntimeError("desktop pixel format is not supported")
        self._bytes_per_pixel = bpp // 8
        red_max, green_max, blue_max = struct.unpack(">HHH", init[8:14])
        red_shift, green_shift, blue_shift = init[14], init[15], init[16]
        name_length = struct.unpack(">I", init[20:24])[0]
        if name_length > 4096:
            raise RuntimeError("desktop framebuffer name is oversized")
        await self._expect(name_length)
        # SetPixelFormat: type + pad + the same 16-byte PIXEL_FORMAT.
        await self._send(
            struct.pack(
                ">B3xBBBBHHHBBB3x",
                0,
                bpp,
                24,
                0,
                1,
                red_max,
                green_max,
                blue_max,
                red_shift,
                green_shift,
                blue_shift,
            )
        )
        await self._send(struct.pack(">BxH", 2, 1) + struct.pack(">i", RAW_ENCODING))
        self._frame = bytearray(self.width * self.height * self._bytes_per_pixel)

    async def _handshake_security(self, server: bytes) -> None:
        # "RFB 003.008\n" -> the 7 version digits start at offset 4.
        version = server[4:11]
        modern = version >= b"003.007"
        if modern:
            count = (await self._expect(1))[0]
            types = await self._expect(count)
        else:
            # RFB 3.3 announces one security type as a U32.
            types = bytes([(await self._expect(4))[3]])
        if SEC_NONE in types:
            choice = SEC_NONE
        elif SEC_VNC_AUTH in types and self._password is not None:
            choice = SEC_VNC_AUTH
        else:
            raise RuntimeError("desktop requires credentials the node cannot provide")
        await self._send(struct.pack(">B", choice))
        if choice == SEC_VNC_AUTH:
            challenge = await self._expect(16)
            await self._send(vnc_auth_response(self._password or "", challenge))
        # RFB 3.8 reports a SecurityResult even for None; 3.3 and 3.7 do not.
        if version >= b"003.008":
            result = struct.unpack(">I", await self._expect(4))[0]
            if result != SERVER_OK:
                raise RuntimeError("desktop rejected the offered credential")

    def resolve_region(self, region: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
        """Clamp a requested rectangle to the framebuffer, refusing empty ones."""

        if region is None:
            return 0, 0, self.width, self.height
        x, y, width, height = region
        if width <= 0 or height <= 0:
            raise ValueError("screenshot region must have a positive size")
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError("screenshot region is outside the desktop")
        return x, y, width, height

    async def screenshot(
        self,
        *,
        region: tuple[int, int, int, int] | None = None,
        scale: float = 1.0,
    ) -> tuple[int, int, bytes]:
        """Fetch one frame (optionally a sub-rectangle) as PNG.

        Vision cost scales with pixels, so callers can ask for the area they
        actually need and a readable downscale instead of the whole screen.
        """

        assert self._reader is not None and self._writer is not None
        if not 0.2 <= scale <= 1.0:
            raise ValueError("screenshot scale must be between 0.2 and 1.0")
        left, top, box_width, box_height = self.resolve_region(region)
        self._frame[:] = bytes(len(self._frame))
        covered_pixels = 0
        expected_pixels = box_width * box_height
        # FramebufferUpdateRequest is type, incremental, x, y, width, height.
        # Non-incremental (0) forces a full repaint of the requested rectangle.
        await self._send(
            struct.pack(">BBHHHH", 3, 0, left, top, box_width, box_height)
        )
        for _ in range(64):
            header = await self._expect(4)
            message_type = header[0]
            if message_type == 0:
                rects = struct.unpack(">H", header[2:4])[0]
                for _ in range(rects):
                    rect = await self._expect(12)
                    _, _, width, height = struct.unpack(">HHHH", rect[:8])
                    encoding = struct.unpack(">i", rect[8:12])[0]
                    if encoding != RAW_ENCODING:
                        raise RuntimeError("desktop sent a non-raw update")
                    data = await self._expect(width * height * self._bytes_per_pixel)
                    self._blit(rect, data)
                    # Count only the part inside the requested rectangle: a
                    # server may return rects that overlap it or extend past it.
                    rect_x, rect_y = struct.unpack(">HH", rect[:4])
                    inside_w = max(
                        0, min(rect_x + width, left + box_width) - max(rect_x, left)
                    )
                    inside_h = max(
                        0, min(rect_y + height, top + box_height) - max(rect_y, top)
                    )
                    covered_pixels += inside_w * inside_h
            elif message_type == 2:  # Bell
                continue
            elif message_type == 3:  # ServerCutText
                length = struct.unpack(">I", await self._expect(4))[0]
                await self._expect(length)
            else:
                raise RuntimeError("desktop sent an unexpected message type")
            if covered_pixels >= expected_pixels:
                break
        else:
            raise RuntimeError("desktop did not deliver a complete frame")
        image = Image.frombytes(
            "RGB",
            (self.width, self.height),
            bytes(self._frame),
            "raw",
            "BGRX" if self._bytes_per_pixel == 4 else "BGR",
        )
        if region is not None:
            image = image.crop((left, top, left + box_width, top + box_height))
        if scale < 1.0:
            image = image.resize(
                (max(1, round(box_width * scale)), max(1, round(box_height * scale))),
                Resampling.LANCZOS,
            )
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return image.width, image.height, buffer.getvalue()

    def _blit(self, rect: bytes, data: bytes) -> None:
        x, y, width, height = struct.unpack(">HHHH", rect[:8])
        stride = self.width * self._bytes_per_pixel
        row_bytes = width * self._bytes_per_pixel
        for row in range(height):
            start = (y + row) * stride + x * self._bytes_per_pixel
            self._frame[start : start + row_bytes] = data[row * row_bytes : (row + 1) * row_bytes]

    async def pointer(self, x: int, y: int, mask: int) -> None:
        if not 0 <= x < self.width or not 0 <= y < self.height or not 0 <= mask <= 0xFF:
            raise ValueError("pointer event is outside the desktop")
        await self._send(struct.pack(">BBHH", 5, mask, x, y))

    async def key(self, keysym: int, *, down: bool) -> None:
        await self._send(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._writer = None
        self._reader = None

    async def _send(self, data: bytes) -> None:
        assert self._writer is not None
        self._writer.write(data)
        await self._writer.drain()

    async def _expect(self, count: int) -> bytes:
        assert self._reader is not None
        if count == 0:
            return b""
        return await asyncio.wait_for(self._reader.readexactly(count), timeout=15)


async def click(client: RfbClient, x: int, y: int, button: str = "left") -> None:
    mask = BUTTON_MASKS[button]
    if button not in {"left", "middle", "right"}:
        raise ValueError("click requires a mouse button")
    await client.pointer(x, y, mask)
    await asyncio.sleep(0.02)
    await client.pointer(x, y, 0)


async def scroll(client: RfbClient, x: int, y: int, direction: str, amount: int = 3) -> None:
    button = {"up": "wheel_up", "down": "wheel_down"}.get(direction)
    if button is None or not 1 <= amount <= 10:
        raise ValueError("scroll direction or amount is invalid")
    mask = BUTTON_MASKS[button]
    for _ in range(amount):
        await client.pointer(x, y, mask)
        await asyncio.sleep(0.02)
        await client.pointer(x, y, 0)
        await asyncio.sleep(0.02)


async def type_text(client: RfbClient, text: str, *, limit: int = 2000) -> None:
    if not text or len(text) > limit:
        raise ValueError("text length is outside the allowed range")
    for character in text:
        keysym = keysym_for(character)
        if keysym is None:
            raise ValueError("text contains a character with no keysym")
        await client.key(keysym, down=True)
        await client.key(keysym, down=False)
        await asyncio.sleep(0.005)


async def press_keys(client: RfbClient, names: list[str]) -> None:
    if not 1 <= len(names) <= 4:
        raise ValueError("key combination length is outside the allowed range")
    keysyms: list[int] = []
    for name in names:
        keysym: int | None = keysym_for(name)
        if keysym is None:
            raise ValueError("unknown key name")
        keysyms.append(keysym)
    for keysym in keysyms:
        await client.key(keysym, down=True)
    for keysym in reversed(keysyms):
        await client.key(keysym, down=False)
