"""bin/obs-browser-refresh runs every five minutes on every OBS pod, so what it
decides to touch matters as much as how it judges a frame: a uniform frame is
blank, a single differing pixel is not, whatever the bit depth Qt writes — and
only the blank browser sources get their renderer respawned, by a swap that
hands each one its own url back."""

from __future__ import annotations

import base64
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


# --- refresh_blank: which sources get the swap, and what the swap writes ---


class _Reply(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


BLANK = bmp([b"\0\0\0\xff"] * 4, width=2, bpp=32)
LIT = bmp([b"\0\0\0\xff"] * 3 + [b"\xff\0\0\xff"], width=2, bpp=32)


class _Client:
    """Every browser source's url, and the frame it renders; a source with no
    url is file-backed. Every call is recorded in order, so a test can assert
    what happened between two writes and not only that both happened."""

    def __init__(self, inputs, urls, frames):
        self.inputs, self.urls, self.frames = inputs, urls, frames
        self.calls = []

    def get_input_list(self):
        return _Reply(inputs=[{"inputName": n, "inputKind": k} for n, k in self.inputs])

    def get_input_settings(self, name):
        url = self.urls.get(name)
        return _Reply(input_settings={"url": url} if url else {"local_file": "x.html"})

    def get_source_screenshot(self, name, fmt, width, height, quality):
        self.calls.append(("shot", name))
        data = base64.b64encode(self.frames[name]).decode()
        return _Reply(image_data=f"data:image/{fmt};base64,{data}")

    def set_input_settings(self, name, settings, overlay):
        self.calls.append(("write", name, settings, overlay))

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))


def _scene():
    return _Client(
        inputs=[
            ("left rotating", "browser_source"),
            ("right rotating", "browser_source"),
            ("Dashcam", "ffmpeg_source"),
        ],
        urls={
            "left rotating": "http://onscreens/left",
            "right rotating": "http://onscreens/right",
        },
        frames={"left rotating": LIT, "right rotating": BLANK},
    )


def test_only_the_blank_browser_sources_are_reloaded():
    # The lit one is left alone: a swap on a source with content is a visible
    # flicker on the stream, every five minutes, for nothing.
    client = _scene()
    assert refresh.refresh_blank(client, sleep=client.sleep) == (2, 1)
    assert [c for c in client.calls if c[0] == "write"] == [
        ("write", "right rotating", {"url": refresh.BLANK_URL}, True),
        ("write", "right rotating", {"url": "http://onscreens/right"}, True),
    ]


def test_the_source_sits_on_about_blank_for_the_gap_before_its_own_url_returns():
    # about:blank, then the gap CEF needs to tear the renderer down, then the
    # url the source had — not a url from another source, and never a
    # replacing write, which would drop the source's size and css.
    client = _scene()
    refresh.refresh_blank(client, sleep=client.sleep)
    assert client.calls[client.calls.index(("shot", "right rotating")) + 1 :] == [
        ("write", "right rotating", {"url": refresh.BLANK_URL}, True),
        ("sleep", refresh.RELOAD_GAP_S),
        ("write", "right rotating", {"url": "http://onscreens/right"}, True),
    ]


def test_a_media_source_is_neither_screenshotted_nor_touched():
    client = _scene()
    refresh.refresh_blank(client, sleep=client.sleep)
    assert not [c for c in client.calls if c[1] == "Dashcam"]


def test_a_file_backed_browser_source_is_skipped_before_the_screenshot():
    # No url means nothing to swap back to; a screenshot of it would only cost
    # a round-trip and, if blank, a swap that leaves it on about:blank.
    client = _Client(
        inputs=[("Slate", "browser_source")], urls={}, frames={"Slate": BLANK}
    )
    assert refresh.refresh_blank(client, sleep=client.sleep) == (1, 0)
    assert client.calls == []


def test_no_browser_sources_means_nothing_checked():
    client = _Client(inputs=[("Dashcam", "ffmpeg_source")], urls={}, frames={})
    assert refresh.refresh_blank(client, sleep=client.sleep) == (0, 0)
