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

# The hardware-encoder plumbing: the env that selects VAAPI, the resource the
# Intel device plugin hands out for it, and the rpi5 worker that has neither.
VAAPI_ENCODER = "ffmpeg_vaapi_tex"
IGPU_RESOURCE = "gpu.intel.com/i915"
RPI5_BOARD_LABEL = "dana.lol/board"
RPI5_TAINT_KEY = "dana.lol/rpi5"


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


def _obs_container(docs: list[dict]) -> dict:
    """The OBS container itself, not the init container beside it."""
    deploys = _by_kind(docs, "Deployment")
    assert len(deploys) == 1, [d["metadata"]["name"] for d in deploys]
    containers = deploys[0]["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, [c["name"] for c in containers]
    return containers[0]


def _pod_spec(docs: list[dict]) -> dict:
    return _by_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]


def _obs_config(docs: list[dict]) -> dict[str, str]:
    """The ConfigMap the OBS container reads its settings from.

    Resolved through the container's `envFrom` rather than by name, so a
    ConfigMap that stops being consumed reads as missing instead of as correct.
    """
    wanted = {
        source["configMapRef"]["name"]
        for source in _obs_container(docs).get("envFrom", [])
        if "configMapRef" in source
    }
    data: dict[str, str] = {}
    for cm in _by_kind(docs, "ConfigMap"):
        if cm["metadata"]["name"] in wanted:
            data.update(cm["data"])
    return data


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_the_encoder_and_the_igpu_claim_agree(manifest: Path):
    """VAAPI needs the i915 device the plugin hands out, and x264 must not hold one.

    `OBS_STREAM_ENCODER=ffmpeg_vaapi_tex` on a pod without
    `gpu.intel.com/i915` gets no render node: OBS logs the failure once and
    encodes in software instead, so the stream stays up at half the quality and
    nothing alerts. The other direction wastes a slot — the minipc's iGPU
    budget is two live encoders, so an x264 pod holding one can keep a real
    VAAPI encoder Pending.
    """
    docs = _docs(manifest)
    encoder = _obs_config(docs)["OBS_STREAM_ENCODER"]
    resources = _obs_container(docs)["resources"]
    requests = resources["requests"]
    limits = resources["limits"]
    if encoder == VAAPI_ENCODER:
        assert requests.get(IGPU_RESOURCE) == "1", (
            f"{manifest.stem} asks for {encoder} without claiming "
            f"{IGPU_RESOURCE}; OBS falls back to software encoding"
        )
        # The device plugin only allocates when the two match; a limit-only or
        # request-only spec is rejected or silently unscheduled.
        assert limits.get(IGPU_RESOURCE) == requests[IGPU_RESOURCE], (
            f"{manifest.stem} requests and limits differ for {IGPU_RESOURCE}"
        )
    else:
        assert IGPU_RESOURCE not in requests and IGPU_RESOURCE not in limits, (
            f"{manifest.stem} encodes with {encoder} but holds an iGPU slot"
        )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_a_vaapi_obs_never_prefers_the_rpi5_worker(manifest: Path):
    """The Pi 5 has no H.264 encoder, so the i915 claim and its affinity exclude.

    A pod carrying both is unschedulable rather than wrong — the arm64 node has
    no i915 to allocate — but it lands as a preference, so it reads like a hint
    and costs a platform its scale-up.
    """
    docs = _docs(manifest)
    if IGPU_RESOURCE not in _obs_container(docs)["resources"]["requests"]:
        return
    spec = _pod_spec(docs)
    assert RPI5_BOARD_LABEL not in yaml.safe_dump(spec.get("affinity", {})), (
        f"{manifest.stem} claims the iGPU and still prefers the rpi5 worker"
    )
    assert RPI5_TAINT_KEY not in yaml.safe_dump(spec.get("tolerations", [])), (
        f"{manifest.stem} claims the iGPU and still tolerates the rpi5 taint"
    )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_novnc_and_obs_server_stay_two_separate_ports(manifest: Path):
    """The split is what lets an Ingress publish one and not the other.

    Every port here is named once on the container and republished under the
    same name by the Service, because the Ingress backend selects noVNC *by
    name* — a Service port pointing its `targetPort` at the wrong container
    port would move obs-server's shutdown route onto the public hostname
    without changing anything the Ingress says.
    """
    docs = _docs(manifest)
    container_ports = {
        p["name"]: p["containerPort"] for p in _obs_container(docs)["ports"]
    }
    assert container_ports["novnc"] == NOVNC_PORT
    assert container_ports["obs-server"] == OBS_SERVER_PORT
    assert NOVNC_PORT != OBS_SERVER_PORT, "the two surfaces share a port"

    services = [
        svc for svc in _by_kind(docs, "Service") if svc["spec"]["type"] == "ClusterIP"
    ]
    assert len(services) == 1, [s["metadata"]["name"] for s in services]
    for port in services[0]["spec"]["ports"]:
        assert port["port"] == container_ports[port["name"]], (
            f"{manifest.stem}: Service port {port['name']} publishes "
            f"{port['port']} against container port {container_ports[port['name']]}"
        )
        assert port["targetPort"] == port["name"], (
            f"{manifest.stem}: Service port {port['name']} targets "
            f"{port['targetPort']} rather than the container port of that name"
        )


def test_the_music_claim_is_mounted_exactly_where_its_sync_gate_runs():
    """Mount and PreSync gate are one decision, and neither belongs on k3d.

    The claim is node-local to the minipc, so an env that grows a mount without
    a node to bind on leaves OBS Pending — and a mount without the gate lets
    Argo tear the running OBS down (Recreate) before anything has checked the
    claim is mountable, which is the teardown the gate exists to hold.
    """
    mounting, gated = set(), set()
    claims = set()
    for manifest in MANIFESTS:
        docs = _docs(manifest)
        if _music_mounts(docs):
            mounting.add(manifest.stem)
            claims |= {
                volume["persistentVolumeClaim"]["claimName"]
                for volume in _pod_spec(docs).get("volumes", [])
                if volume["name"] == "music"
            }
        for job in _by_kind(docs, "Job"):
            if job["metadata"]["name"].endswith("-volume-gate"):
                gated.add(manifest.stem)
                claims |= {
                    volume["persistentVolumeClaim"]["claimName"]
                    for volume in job["spec"]["template"]["spec"]["volumes"]
                }
        # The LoadBalancer only exists on the k3d/local envs, which have no such
        # node — a cheaper tell than reading the cluster out of the filename.
        if any(
            svc["spec"]["type"] == "LoadBalancer" for svc in _by_kind(docs, "Service")
        ):
            assert not _music_mounts(docs), (
                f"{manifest.stem} is a k3d env and mounts the node-local music "
                "claim; the pod would stay Pending on an unbindable volume"
            )
    assert mounting, "no manifest mounts the music share at all"
    assert mounting == gated, (
        f"mounted in {sorted(mounting)} but gated in {sorted(gated)}"
    )
    assert len(claims) == 1, f"the music share is bound under several names: {claims}"
