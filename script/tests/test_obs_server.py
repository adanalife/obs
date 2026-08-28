"""Tests for obs-server, the HTTP shim that lets the admin panel treat OBS
like its sibling Go services.

The point of most of them is `/version`: its four keys are a cross-repo
contract with the console's status table, asserted nowhere until now, in a
repo whose CI ran no Python tests over `script/` at all. Renaming one used to
degrade the console silently.
"""

from __future__ import annotations

import os
import signal
import time

import pytest

import obs_server


@pytest.fixture
def client():
    obs_server.app.config["TESTING"] = True
    return obs_server.app.test_client()


@pytest.fixture
def version_files(tmp_path, monkeypatch):
    """Point the bake-time version files at a tmp dir and hand back the paths,
    so a test can create, omit, or blank either one."""
    version, sha = tmp_path / "version", tmp_path / "sha"
    monkeypatch.setattr(obs_server, "VERSION_FILE", version)
    monkeypatch.setattr(obs_server, "SHA_FILE", sha)
    return version, sha


def test_ready_answers_ok(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "OK\n"


def test_version_carries_exactly_the_four_contract_keys(client, version_files):
    """The shape the console parses identically across all four services. The
    assertion is on the whole key set, not a subset: an added key is as much a
    contract change as a renamed one, and should be a deliberate edit here."""
    version, sha = version_files
    version.write_text("2.13.0\n")
    sha.write_text("abc1234\n")

    body = client.get("/version").get_json()

    assert set(body) == {"tag", "sha", "built_at", "started_at"}
    assert body["tag"] == "2.13.0"
    assert body["sha"] == "abc1234"
    # started_at is what the console's uptime ticker counts from, so it has to
    # parse as a timestamp rather than merely be present.
    assert body["started_at"].startswith(
        time.strftime("%Y", time.gmtime(obs_server.started_at))
    )
    assert body["built_at"]


def test_version_falls_back_when_the_bake_step_did_not_run(client, version_files):
    """A local docker build with no /etc/tripbot/* files still answers the full
    shape — the console renders a row for it rather than erroring on a 500."""
    body = client.get("/version").get_json()

    assert set(body) == {"tag", "sha", "built_at", "started_at"}
    assert body["tag"] == "dev"
    assert body["sha"] == ""
    # No version file means no mtime to report, which is said as empty rather
    # than as a fabricated time.
    assert body["built_at"] == ""


@pytest.mark.parametrize("contents", ["", "   ", "\n", "\t\n  "])
def test_a_blank_version_file_reads_as_the_default(version_files, contents):
    """Whitespace-only is deliberately treated as missing: a bake step that ran
    but wrote nothing is not a version, and `tag: " "` would render as a blank
    cell in the console with no hint that anything was wrong."""
    version, _ = version_files
    version.write_text(contents)

    assert obs_server._read_or_default(version, "dev") == "dev"


def test_shutdown_answers_before_it_signals(client, monkeypatch):
    """The ordering the endpoint's docstring explains at length: the 202 has to
    reach the admin panel before the container starts dying, or the panel sees
    a dropped connection instead of an acknowledged restart."""
    signalled = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    monkeypatch.setattr(obs_server, "SHUTDOWN_DELAY_SECONDS", 0.05)

    resp = client.post("/admin/shutdown")

    assert resp.status_code == 202
    assert resp.get_data(as_text=True) == "shutting down\n"
    # Still nothing signalled at the moment the response is in hand.
    assert signalled == []

    deadline = time.monotonic() + 5
    while not signalled and time.monotonic() < deadline:
        time.sleep(0.01)
    # Supervisord is PID 1; SIGTERMing it is what takes the container down and
    # lets k8s respawn the pod.
    assert signalled == [(obs_server.SUPERVISORD_PID, signal.SIGTERM)]


def test_shutdown_survives_a_supervisord_that_is_already_gone(monkeypatch):
    """The timer thread has no caller left to raise into, so a failed kill must
    be logged and swallowed rather than dying in the background."""

    def boom(pid, sig):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(os, "kill", boom)

    obs_server._fire_shutdown()  # must not raise
