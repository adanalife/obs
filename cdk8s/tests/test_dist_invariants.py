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
