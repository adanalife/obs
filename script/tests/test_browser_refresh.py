"""The blank-frame judgement in bin/obs-browser-refresh decides which browser
sources get their renderer respawned, so it is the one piece worth pinning:
a uniform frame is blank, a single differing pixel is not, whatever the
bit depth Qt happens to write."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import struct

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "bin" / "obs-browser-refresh"
loader = importlib.machinery.SourceFileLoader("obs_browser_refresh", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
refresh = importlib.util.module_from_spec(spec)
loader.exec_module(refresh)


def bmp(pixels: list[bytes], width: int, bpp: int) -> bytes:
    """A minimal BMP: 14-byte file header + 40-byte BITMAPINFOHEADER + rows,
    padded to four bytes like a real writer does."""
    stride = bpp // 8
    row_len = -(-width * stride // 4) * 4
    height = len(pixels) // width
    rows = b""
    for r in range(height):
        row = b"".join(pixels[r * width : (r + 1) * width])
        rows += row.ljust(row_len, b"\0")
    header = struct.pack("<2sIHHI", b"BM", 54 + len(rows), 0, 0, 54)
    info = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, bpp, 0, len(rows), 2835, 2835, 0, 0
    )
    return header + info + rows


@pytest.mark.parametrize("bpp", [24, 32])
def test_uniform_frame_is_blank(bpp):
    px = b"\0" * (bpp // 8)
    assert refresh.is_blank(bmp([px] * 6, width=3, bpp=bpp))


@pytest.mark.parametrize("bpp", [24, 32])
def test_one_lit_pixel_is_not_blank(bpp):
    px = b"\0" * (bpp // 8)
    lit = b"\xff" + b"\0" * (bpp // 8 - 1)
    frame = [px] * 5 + [lit]
    assert not refresh.is_blank(bmp(frame, width=3, bpp=bpp))


def test_uniform_nonblack_frame_is_blank():
    # Blank means uniform, not black: a solid colour frame is still a dead page.
    px = b"\x10\x20\x30\xff"
    assert refresh.is_blank(bmp([px] * 4, width=2, bpp=32))


def test_rejects_non_bmp():
    with pytest.raises(ValueError):
        refresh.is_blank(b"\x89PNG" + b"\0" * 40)


def test_decode_screenshot_strips_data_uri():
    assert refresh.decode_screenshot("data:image/bmp;base64,Qk0=") == b"BM"
