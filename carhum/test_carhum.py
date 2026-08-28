"""Synthesis smoke test for carhum.py.

The car-hum bed is what the audio watchdog swaps to when SomaFM drops, so a
synthesis regression surfaces as the fallback sounding wrong on the live
stream — or as silence. Nothing else in CI runs this code: the image build
renders the variants, but a render that produced two minutes of digital
silence would still succeed.

Driven as a subprocess because carhum.py declares its numpy/scipy deps inline
(PEP 723), so `uv run` resolves them without them entering this project's
groups. The renders are read back with stdlib `wave` — the assertions are about
the file's shape and level, which needs no array library.
"""

import array
import subprocess
import sys
import wave
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "carhum.py"
SR = 48000
DURATION = 2.0
# carhum.py normalizes the peak to --peak-dbfs (default -3 dBFS) before the
# int16 cast, so a correct render lands within a sample or two of this.
PEAK_DBFS_FLOOR = int(32767 * 10 ** (-3.5 / 20))
PEAK_CEILING = 32767


def render(tmp_path, *args, name="out.wav"):
    out = tmp_path / name
    cmd = ["uv", "run", str(SCRIPT), "--duration", str(DURATION), "--seed", "1"]
    cmd += [*args, "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"carhum.py failed:\n{proc.stderr}"
    return out


def read(path):
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 2, "the bed is stereo by design (per-channel noise)"
        assert w.getsampwidth() == 2, "int16 PCM"
        assert w.getframerate() == SR
        frames = w.getnframes()
        samples = array.array("h", w.readframes(frames))
    return frames, samples


@pytest.mark.skipif(
    subprocess.run(["uv", "--version"], capture_output=True).returncode != 0,
    reason="needs uv to resolve carhum.py's inline numpy/scipy deps",
)
def test_render_is_audible_audio_of_the_requested_length(tmp_path):
    frames, samples = read(render(tmp_path))
    assert frames == int(DURATION * SR)

    peak = max(abs(s) for s in samples)
    assert PEAK_DBFS_FLOOR <= peak <= PEAK_CEILING, (
        f"peak {peak} is outside the -3 dBFS normalization window — "
        "the render is silent, clipped, or unnormalized"
    )

    # Silence with a single loud sample would pass a peak check. The bed is
    # broadband noise, so most of it should be well off zero.
    loud = sum(1 for s in samples if abs(s) > peak // 10)
    assert loud > len(samples) // 2, "the bed is mostly silence, not a drone"


@pytest.mark.skipif(
    subprocess.run(["uv", "--version"], capture_output=True).returncode != 0,
    reason="needs uv to resolve carhum.py's inline numpy/scipy deps",
)
def test_seed_and_preset_decide_the_render(tmp_path):
    # render-variants.sh pins a seed per variant so the image build is
    # reproducible; that only holds if the seed fully determines the output.
    a = render(tmp_path, name="a.wav").read_bytes()
    b = render(tmp_path, name="b.wav").read_bytes()
    assert a == b, "same seed rendered different audio"

    # Each preset is a distinct voicing, not a relabelling of the default.
    idle = render(tmp_path, "--preset", "idle", name="idle.wav").read_bytes()
    highway = render(tmp_path, "--preset", "highway", name="highway.wav").read_bytes()
    assert idle != highway != a


@pytest.mark.skipif(
    subprocess.run(["uv", "--version"], capture_output=True).returncode != 0,
    reason="needs uv to resolve carhum.py's inline numpy/scipy deps",
)
def test_seamless_loop_trims_one_crossfade(tmp_path):
    # render-variants.sh renders every shipped bed with --loop, so the looped
    # path is the one that actually reaches the stream. The tail is crossfaded
    # back over the head, costing exactly one crossfade of length.
    xfade = 0.5
    frames, _ = read(render(tmp_path, "--loop", str(xfade), name="loop.wav"))
    assert frames == int(DURATION * SR) - int(xfade * SR)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
