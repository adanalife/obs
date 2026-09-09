"""Invariants the synthed OBS manifests must hold, whatever else changes.

Read from the committed `cdk8s/dist/` rather than re-synthed in-process, which is
deliberate and buys two things. The dist is what Argo actually applies, so a
hand-edit to it is caught as well as a config change; and `cdk8s-synth.yml`
already fails when dist and a fresh synth disagree, so reading dist cannot drift
from reading the code.

The gap these fill: `cdk8s-synth.yml` proves dist matches the code, and says
nothing about whether either is *right*. A flip in one of the values below
changes the golden files, a reviewer sees a plausible diff, and nothing states the
rule that was broken. Same argument as infra's cdk8s unit suite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "cdk8s" / "dist"

# The noVNC port, from the same generated contract the manifests are built from,
# so this test cannot pass by agreeing with a stale number.
NOVNC_PORT: int = json.loads((REPO / "contract.json").read_text())["ports"]["obs_novnc"]
OBS_SERVER_PORT: int = json.loads((REPO / "contract.json").read_text())["ports"][
    "obs_server"
]

MANIFESTS = sorted(DIST.glob("*-obs-*.k8s.yaml"))


def _music_dir() -> str:
    """The share path `script/background-audio.sh` scans, read from the script.

    Taken from the shell rather than repeated here for the same reason the ports
    above come from `contract.json`: a test carrying its own copy of the literal
    passes happily while the two sides drift apart.
    """
    line = re.search(
        r'^MUSIC_DIR="\$\{MUSIC_DIR:-([^}]+)\}"',
        (REPO / "script" / "background-audio.sh").read_text(),
        re.MULTILINE,
    )
    assert line, "MUSIC_DIR is not where this test expects it in background-audio.sh"
    return line.group(1)


MUSIC_DIR: str = _music_dir()


def _music_mounts(docs: list[dict]) -> list[dict]:
    """Every volumeMount named `music` across the instance's Deployment."""
    mounts = []
    for deploy in _by_kind(docs, "Deployment"):
        for container in deploy["spec"]["template"]["spec"]["containers"]:
            mounts += [
                m for m in container.get("volumeMounts", []) if m["name"] == "music"
            ]
    return mounts


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def _service_ports(docs: list[dict]) -> dict[str, int]:
    """Every port the instance's Service publishes, keyed by name.

    An Ingress backend may name a port instead of numbering it, so resolving a
    name needs the Service that defines it.
    """
    ports: dict[str, int] = {}
    for svc in _by_kind(docs, "Service"):
        for port in svc["spec"].get("ports", []):
            ports[port["name"]] = port["port"]
    return ports


def _backend_ports(docs: list[dict]) -> list[tuple[str, int]]:
    """(ingress name, resolved port) for every backend any Ingress publishes.

    Covers both shapes the two Ingresses use: traefik names the port (`novnc`)
    and the tailscale one numbers it (`defaultBackend`, 6080).
    """
    names = _service_ports(docs)
    found = []
    for ing in _by_kind(docs, "Ingress"):
        ing_name = ing["metadata"]["name"]
        backends = []
        if "defaultBackend" in ing["spec"]:
            backends.append(ing["spec"]["defaultBackend"])
        for rule in ing["spec"].get("rules", []):
            backends.extend(rule.get("http", {}).get("paths", []))
        for backend in backends:
            port = backend.get("backend", backend)["service"]["port"]
            resolved = port.get("number") or names[port["name"]]
            found.append((ing_name, resolved))
    return found


def test_there_are_manifests_to_check():
    """A glob that matches nothing would make every test below vacuously pass."""
    assert len(MANIFESTS) >= 10, MANIFESTS


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_ingresses_publish_only_novnc(manifest: Path):
    """No Ingress may reach anything but noVNC — obs-server especially.

    obs-server serves `POST /admin/shutdown`, which SIGTERMs supervisord and so
    restarts the container feeding the live stream. It is unauthenticated, because
    it is reached in-namespace by the console's Service. Pointing an Ingress at it
    — by switching a port name, or by numbering the wrong port — would publish a
    "stop the stream" button on a public hostname, and the change would read as a
    one-word diff. noVNC is behind the same Ingress class but is the surface the
    Ingress exists for.
    """
    for ing_name, port in _backend_ports(_docs(manifest)):
        assert port != OBS_SERVER_PORT, (
            f"{ing_name} publishes obs-server ({port}) — that exposes "
            "POST /admin/shutdown, which restarts the streaming container"
        )
        assert port == NOVNC_PORT, (
            f"{ing_name} publishes port {port}; only noVNC ({NOVNC_PORT}) "
            "belongs on an Ingress"
        )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_every_obs_is_born_parked(manifest: Path):
    """Replica counts are runtime-owned, so git may only declare zero.

    Argo ignores `.spec.replicas` on these Deployments and the console's scale-up
    is what brings a platform live, so the declared count is the birth state — and
    the state a Deployment returns to whenever the object is recreated. A `1` here
    would start an OBS nobody asked for, which on the minipc means claiming one of
    two VAAPI slots away from a live encoder. That has happened before, from the
    other direction: stage OBS silently claiming the iGPU.
    """
    deploys = _by_kind(_docs(manifest), "Deployment")
    assert len(deploys) == 1, [d["metadata"]["name"] for d in deploys]
    assert deploys[0]["spec"]["replicas"] == 0, (
        f"{deploys[0]['metadata']['name']} declares "
        f"{deploys[0]['spec']['replicas']} replicas; every OBS births parked"
    )


def test_tailscale_ingresses_use_the_shared_proxy_group():
    """A tailscale Ingress without the proxy-group annotation gets its own pod.

    The operator's default is one dedicated proxy pod per Ingress; the annotation
    is what hands the hostname to the shared HA fleet instead. Dropping it is a
    silent regression — the endpoint still resolves and serves the same
    `*.ts.net` name, so nothing fails except the proxy pod count, which no other
    check reads.
    """
    found = [
        (manifest.stem, ing)
        for manifest in MANIFESTS
        for ing in _by_kind(_docs(manifest), "Ingress")
        if ing["spec"].get("ingressClassName") == "tailscale"
    ]
    # One per platform per tailnet-publishing env; zero would make the loop
    # below vacuous.
    assert len(found) >= 10, [name for name, _ in found]
    for manifest_name, ing in found:
        annotations = ing["metadata"].get("annotations", {})
        assert annotations.get("tailscale.com/proxy-group") == "ingress-proxies", (
            f"{manifest_name}: {ing['metadata']['name']} has no proxy-group "
            "annotation, so the operator gives it a dedicated proxy pod"
        )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_the_music_share_is_mounted_where_the_bed_looks_for_it(manifest: Path):
    """The mount path and the path the album bed scans are one contract.

    `script/background-audio.sh` walks MUSIC_DIR for tracks and falls back to the
    carhum drone when it finds none. A mount at any other path is therefore not a
    crash: OBS boots, the album bed finds an empty directory, and the stream plays
    the drone for as long as nobody notices. Only prose held the two together.
    """
    for mount in _music_mounts(_docs(manifest)):
        assert mount["mountPath"] == MUSIC_DIR, (
            f"the music share mounts at {mount['mountPath']} but the album bed "
            f"scans {MUSIC_DIR}; the bed would find no tracks and play the drone"
        )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_the_music_share_is_mounted_read_only(manifest: Path):
    """Nothing in OBS has any business writing to the licensed album library.

    The claim is node-local and shared by every platform's OBS on the one node it
    lives on, so a writable mount puts five containers a bug away from deleting a
    library that is restaged by hand.
    """
    docs = _docs(manifest)
    for mount in _music_mounts(docs):
        assert mount.get("readOnly") is True, (
            f"the music share mounts writable in {manifest.stem}"
        )
    for deploy in _by_kind(docs, "Deployment"):
        for volume in deploy["spec"]["template"]["spec"].get("volumes", []):
            if volume["name"] == "music":
                assert volume["persistentVolumeClaim"].get("readOnly") is True, (
                    f"the music claim is bound writable in {manifest.stem}"
                )


@pytest.mark.parametrize(
    "manifest",
    [m for m in MANIFESTS if m.stem.startswith("prod-")],
    ids=lambda p: p.stem,
)
def test_prod_obs_carries_the_stream_priority_class(manifest: Path):
    """Prod OBS must outrank everything else scheduled on the minipc.

    Every platform's OBS and the whole rest of the fleet share one node, and that
    node's internal NVMe already stalls under load. Without the priority class a
    prod encoder is an ordinary eviction candidate, and evicting one is a stream
    going off air rather than a pod restarting quietly.
    """
    for deploy in _by_kind(_docs(manifest), "Deployment"):
        assert (
            deploy["spec"]["template"]["spec"].get("priorityClassName") == "prod-stream"
        ), f"{manifest.stem} does not claim the prod-stream priority class"
