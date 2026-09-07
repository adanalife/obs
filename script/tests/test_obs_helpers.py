"""The break-glass `bin/obs-*` helpers, exercised against a fake OBS client.

These are the operator's incident path, so a broken one is discovered
mid-incident with the stream already down. There is no OBS to talk to in CI,
but the decisions each script makes before and after the WebSocket call are
plain Python — which source kinds to touch, what gets masked, where a key is
allowed to come from — and those are what the fakes below pin.

Loaded as modules the way test_browser_refresh.py loads its script: the
helpers import obsws_python lazily inside main(), so importing one here costs
nothing and needs no dependency.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import pathlib

import pytest

BIN = pathlib.Path(__file__).resolve().parents[2] / "bin"


def load(name):
    loader = importlib.machinery.SourceFileLoader(
        name.replace("-", "_"), str(BIN / name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


repoint = load("obs-input-repoint")
media_restart = load("obs-media-restart")
screenshot_check = load("obs-screenshot-check")
key_rotate = load("obs-stream-key-rotate")


class _Reply(dict):
    """obsws-python returns attribute-style replies; dict keeps the fakes short."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


# --- obs-media-restart: only media sources, never the overlays ---


def test_only_ffmpeg_sources_are_restarted():
    # Restarting a browser_source is not a no-op — it would take the overlays
    # down alongside the video this script exists to reconnect.
    inputs = [
        {"inputName": "Dashcam", "inputKind": "ffmpeg_source"},
        {"inputName": "left rotating", "inputKind": "browser_source"},
        {"inputName": "Background Audio", "inputKind": "ffmpeg_source"},
        {"inputName": "Broken video warning", "inputKind": "text_gdiplus_v3"},
    ]
    assert [i["inputName"] for i in media_restart.media_sources(inputs)] == [
        "Dashcam",
        "Background Audio",
    ]


def test_no_media_sources_restarts_nothing():
    class _Client:
        def get_input_list(self):
            return _Reply(inputs=[{"inputName": "left", "inputKind": "browser_source"}])

        def trigger_media_input_action(self, *a):
            raise AssertionError("restarted a non-media source")

    assert media_restart.restart_all(_Client()) == 0


def test_every_media_source_gets_the_restart_action():
    restarted = []

    class _Client:
        def get_input_list(self):
            return _Reply(
                inputs=[
                    {"inputName": "Dashcam", "inputKind": "ffmpeg_source"},
                    {"inputName": "Background Audio", "inputKind": "ffmpeg_source"},
                ]
            )

        def trigger_media_input_action(self, name, action):
            restarted.append((name, action))

    assert media_restart.restart_all(_Client()) == 2
    assert restarted == [
        ("Dashcam", "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"),
        ("Background Audio", "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"),
    ]


# --- obs-input-repoint: read-only by default, and a merging write ---


class _RepointClient:
    def __init__(self, current="rtsp://old/live"):
        self.settings = {"input": current, "reconnect_delay_sec": 2}
        self.writes = []

    def get_input_settings(self, source):
        return _Reply(input_settings=dict(self.settings))

    def set_input_settings(self, source, settings, overlay):
        self.writes.append((source, settings, overlay))
        self.settings.update(settings)


def test_no_url_reads_without_writing():
    # Capturing the rollback value is the safe first move in an incident, so
    # the no-argument invocation must not be able to change anything.
    client = _RepointClient()
    assert repoint.repoint(client, "Dashcam", None) == 0
    assert client.writes == []


def test_source_defaults_to_dashcam():
    assert repoint.parse_args([]) == (None, "Dashcam")
    assert repoint.parse_args(["rtsp://new/live"]) == ("rtsp://new/live", "Dashcam")
    assert repoint.parse_args(["rtsp://new/live", "Other"]) == (
        "rtsp://new/live",
        "Other",
    )


def test_the_write_merges_rather_than_replacing():
    # overlay=True is load-bearing: a replacing write would drop
    # reconnect_delay_sec and buffering_mb, which is how the source recovers.
    client = _RepointClient()
    assert repoint.repoint(client, "Dashcam", "rtsp://new/live") == 0
    (source, settings, overlay) = client.writes[0]
    assert (source, overlay) == ("Dashcam", True)
    assert settings == {"input": "rtsp://new/live"}
    assert client.settings["reconnect_delay_sec"] == 2


def test_a_repoint_obs_did_not_take_fails():
    # OBS accepting the request and reporting a different value back is the
    # silent failure this script has to turn into a non-zero exit.
    class _Ignoring(_RepointClient):
        def set_input_settings(self, source, settings, overlay):
            self.writes.append((source, settings, overlay))

    assert repoint.repoint(_Ignoring(), "Dashcam", "rtsp://new/live") == 1


# --- obs-stream-key-rotate: the key never comes from argv, and never prints ---


def test_mask_never_reveals_a_usable_key():
    # Deliberately repetitive rather than key-shaped: a realistic-looking
    # sample trips the repo's secret scanner, and mask() only reads the first
    # four characters and the length.
    key = "live" + "-xxxx" * 6
    masked = key_rotate.mask(key)
    assert key not in masked
    assert masked.startswith("live")
    assert str(len(key)) in masked


def test_a_short_key_reveals_no_prefix_at_all():
    # Four characters of an 8-character key is half of it, so short keys get
    # only their length.
    assert key_rotate.mask("abcdefgh") == "… (8 chars)"
    assert key_rotate.mask("") == "(empty)"


def test_a_key_on_the_command_line_is_refused():
    # argv is world-readable in `ps`, so a key passed there is already leaked;
    # reading it anyway would reward the mistake.
    _, _, error = key_rotate.read_key(["live_secret"], {}, io.StringIO())
    assert error is not None
    assert "argv" in error


def test_the_env_wins_over_stdin():
    dry, key, error = key_rotate.read_key(
        [], {"NEW_STREAM_KEY": "from_env"}, io.StringIO("from_stdin")
    )
    assert (dry, key, error) == (False, "from_env", None)


def test_stdin_is_read_when_the_env_is_unset():
    dry, key, error = key_rotate.read_key([], {}, io.StringIO("  piped_key\n"))
    assert (dry, key, error) == (False, "piped_key", None)


def test_no_key_anywhere_is_an_error_not_an_empty_rotation():
    # An empty key would be accepted by OBS and would take the stream off air.
    _, _, error = key_rotate.read_key([], {}, io.StringIO(""))
    assert error is not None


def test_dry_run_needs_no_key():
    dry, _, error = key_rotate.read_key(["--dry-run"], {}, io.StringIO(""))
    assert (dry, error) == (True, None)


class _KeyClient:
    def __init__(self, live=False):
        self.settings = {
            "server": "rtmp://ingest.example/app",
            "key": "old_key_aaaaaaaaaa",
            "bwtest": False,
        }
        self.live, self.writes = live, []

    def get_stream_service_settings(self):
        return _Reply(
            stream_service_type="rtmp_custom",
            stream_service_settings=dict(self.settings),
        )

    def get_stream_status(self):
        return _Reply(output_active=self.live)

    def set_stream_service_settings(self, service_type, settings):
        self.writes.append((service_type, settings))
        self.settings = dict(settings)


def test_dry_run_reads_and_writes_nothing():
    client = _KeyClient()
    assert key_rotate.rotate(client, "", dry_run=True) == 0
    assert client.writes == []


def test_the_swap_carries_every_other_field_over():
    # SetStreamServiceSettings replaces the whole object rather than merging,
    # so anything not carried across is silently reset to its default.
    client = _KeyClient()
    assert key_rotate.rotate(client, "new_key_bbbbbbbbbb", dry_run=False) == 0
    (service_type, settings) = client.writes[0]
    assert service_type == "rtmp_custom"
    assert settings == {
        "server": "rtmp://ingest.example/app",
        "key": "new_key_bbbbbbbbbb",
        "bwtest": False,
    }


def test_a_swap_obs_did_not_take_fails():
    class _Ignoring(_KeyClient):
        def set_stream_service_settings(self, service_type, settings):
            self.writes.append((service_type, settings))

    assert key_rotate.rotate(_Ignoring(), "new_key_bbbbbbbbbb", dry_run=False) == 1


def test_the_key_is_never_printed_in_full(capsys):
    client = _KeyClient(live=True)
    key_rotate.rotate(client, "new_key_bbbbbbbbbb", dry_run=False)
    out = capsys.readouterr().out
    assert "new_key_bbbbbbbbbb" not in out
    assert "old_key_aaaaaaaaaa" not in out
    assert "ACTIVE" in out  # whether the output was live decides if it took effect


# --- obs-screenshot-check: what gets screenshotted, and what counts as blank ---


class _ShotClient:
    """One group holding two children, plus a top-level source and an audio one.

    `Overlays` is in every size map because the group *container* is
    screenshotted alongside its members — OBS renders a group as a composite,
    so it is a source in its own right.
    """

    def __init__(self, sizes):
        self.sizes, self.asked = sizes, []

    def get_current_program_scene(self):
        return _Reply(current_program_scene_name="Main")

    def get_scene_item_list(self, scene):
        return _Reply(
            scene_items=[
                {
                    "sourceName": "Dashcam",
                    "sceneItemTransform": {"sourceWidth": 1920, "sourceHeight": 1080},
                },
                {
                    "sourceName": "Overlays",
                    "isGroup": True,
                    "sceneItemTransform": {"sourceWidth": 1920, "sourceHeight": 1080},
                },
                {
                    "sourceName": "Background Audio",
                    "sceneItemTransform": {"sourceWidth": 0, "sourceHeight": 0},
                },
            ]
        )

    def get_group_scene_item_list(self, name):
        return _Reply(
            scene_items=[
                {
                    "sourceName": "left rotating",
                    "sceneItemTransform": {"sourceWidth": 600, "sourceHeight": 200},
                },
                {
                    "sourceName": "right rotating",
                    "sceneItemTransform": {"sourceWidth": 600, "sourceHeight": 200},
                },
            ]
        )

    def send(self, request, payload):
        import base64

        name = payload["sourceName"]
        self.asked.append(name)
        return _Reply(
            image_data="data:image/png;base64,"
            + base64.b64encode(b"\0" * self.sizes[name]).decode()
        )


def test_group_members_are_screenshotted_too():
    # The 2026-06-21 outage was a rotator inside a group: a check that only
    # walked top-level items would have called that scene healthy.
    client = _ShotClient(
        {
            "Dashcam": 5000,
            "Overlays": 5000,
            "left rotating": 5000,
            "right rotating": 5000,
        }
    )
    assert screenshot_check.check_scene(client, min_bytes=1000, skip_names=set()) == []
    assert client.asked == ["Dashcam", "Overlays", "left rotating", "right rotating"]


def test_a_blank_source_inside_a_group_is_reported():
    client = _ShotClient(
        {
            "Dashcam": 5000,
            "Overlays": 5000,
            "left rotating": 5000,
            "right rotating": 218,
        }
    )
    failed = screenshot_check.check_scene(client, min_bytes=1000, skip_names=set())
    assert failed == ["right rotating"]


def test_audio_only_sources_are_never_screenshotted():
    # A 0×0 transform makes GetSourceScreenshot fail with code 702, so asking
    # would turn the music bed into a permanent failure.
    client = _ShotClient(
        {
            "Dashcam": 5000,
            "Overlays": 5000,
            "left rotating": 5000,
            "right rotating": 5000,
        }
    )
    screenshot_check.check_scene(client, min_bytes=1000, skip_names=set())
    assert "Background Audio" not in client.asked


def test_a_skipped_source_is_neither_asked_nor_failed():
    client = _ShotClient(
        {
            "Dashcam": 5000,
            "Overlays": 5000,
            "left rotating": 5000,
            "right rotating": 218,
        }
    )
    failed = screenshot_check.check_scene(
        client, min_bytes=1000, skip_names={"right rotating"}
    )
    assert failed == []
    assert "right rotating" not in client.asked


def test_a_source_whose_screenshot_errors_is_a_failure_not_a_skip():
    class _Failing(_ShotClient):
        def send(self, request, payload):
            if payload["sourceName"] == "left rotating":
                raise RuntimeError("code 702")
            return super().send(request, payload)

    client = _Failing({"Dashcam": 5000, "Overlays": 5000, "right rotating": 5000})
    failed = screenshot_check.check_scene(client, min_bytes=1000, skip_names=set())
    assert failed == ["left rotating"]


@pytest.mark.parametrize(
    "value,want",
    [
        (None, set()),
        ("", set()),
        ("  ", set()),
        ("A", {"A"}),
        ("A, B ,, C", {"A", "B", "C"}),
    ],
)
def test_skip_list_parsing_drops_blanks(value, want):
    # A bare split on an unset variable yields {""}, which would skip a source
    # named "" and silently mean something different from "skip nothing".
    assert screenshot_check.parse_skip(value) == want


def test_capture_strips_the_data_uri_prefix():
    client = _ShotClient({"Dashcam": 3})
    assert screenshot_check.capture(client, "Dashcam") == b"\0\0\0"


def test_renders_video_needs_both_dimensions():
    assert screenshot_check.renders_video(
        {"sceneItemTransform": {"sourceWidth": 10, "sourceHeight": 10}}
    )
    assert not screenshot_check.renders_video(
        {"sceneItemTransform": {"sourceWidth": 10, "sourceHeight": 0}}
    )
    assert not screenshot_check.renders_video({})
