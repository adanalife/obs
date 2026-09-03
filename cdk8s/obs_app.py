"""ObsInstance — one OBS deployment for a single streaming platform.

`ObsInstance(platform="youtube", env=...)` emits cleanly-named `obs-youtube`
objects (ConfigMap, Deployment, Service, and — per env — a stream-key
ExternalSecret, a host-access LoadBalancer, and noVNC Ingresses), with an
`app: obs-youtube` selector so a Service only ever selects its own pods. The
per-env overlays (GPU claim, encoder, quality, stream-key toggle, ingress) are
data on EnvConfig.

Everything is cdk8s.ApiObject with literal specs — the same idiom as
platform-gateway and tripbot-console.
"""

from __future__ import annotations

import hashlib
import json

import cdk8s
import contract
from config import EnvConfig
from constructs import Construct

IMAGE = "ghcr.io/adanalife/obs"
PART_OF = "tripbot"
CONFIG_HASH_ANNOTATION = "adanalife.dev/config-hash"

# Multi-arch image carrying the `crane` CLI, used by the PreSync image gate to
# probe the registry. gcr.io (not Docker Hub) — the CI base-image-mirror policy
# doesn't apply to a runtime cluster pull.
CRANE_IMAGE = "gcr.io/go-containerregistry/crane:v0.21.7"

# The volume gate runs `true` — the mount is the whole assertion, so any image
# does. Reuses the ubuntu mirror the Dockerfile's carhum stage already pulls
# rather than introducing a second base to keep current.
VOLUME_GATE_IMAGE = "ghcr.io/adanalife/mirror/ubuntu:24.04"

# How long the onscreens wait tolerates an unready backend before starting OBS
# anyway (attempts × seconds).
_ONSCREENS_WAIT_ATTEMPTS = 60
_ONSCREENS_WAIT_INTERVAL = 2

# The album-bed claim, provisioned by the infra repo and mounted by tripbot too.
# A cross-repo contract on the name: all three have to say the same thing, and a
# mismatch leaves these pods Pending on an unbound claim.
MUSIC_CLAIM = "obs-music-local"

# The ephemeral arm64 rpi5 worker on the minipc cluster — taint repels by
# default, board label is the affinity target. OBS opts in (stage only) ONLY
# while it's a software encoder; a VAAPI OBS must stay on the MS-01's iGPU, so
# the i915 claim + this affinity are mutually exclusive (see the gate below).
_RPI5_TAINT_KEY = "dana.lol/rpi5"
_RPI5_BOARD_LABEL = "dana.lol/board"
_RPI5_BOARD_VALUE = "rpi5"


def _obj(
    scope: Construct,
    id: str,
    *,
    api_version: str,
    kind: str,
    name: str,
    namespace: str,
    labels: dict | None = None,
    annotations: dict | None = None,
    **body,
):
    """ApiObject takes only apiVersion/kind/metadata as props; other top-level
    keys (spec, data, …) land via JsonPatch — the idiom infra's cdk8s, the
    console, and the gateway all use for literal specs. labels/annotations are
    omitted from metadata when None (the ExternalSecret + Ingresses carry
    none)."""
    metadata = {"name": name, "namespace": namespace}
    if labels:
        metadata["labels"] = labels
    if annotations:
        metadata["annotations"] = annotations
    obj = cdk8s.ApiObject(
        scope, id, api_version=api_version, kind=kind, metadata=metadata
    )
    for key, value in body.items():
        obj.add_json_patch(cdk8s.JsonPatch.add(f"/{key}", value))
    return obj


def _prefer_rpi5_affinity() -> dict:
    return {
        "nodeAffinity": {
            "preferredDuringSchedulingIgnoredDuringExecution": [
                {
                    "weight": 100,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": _RPI5_BOARD_LABEL,
                                "operator": "In",
                                "values": [_RPI5_BOARD_VALUE],
                            }
                        ]
                    },
                }
            ]
        }
    }


def _prefer_rpi5_tolerations() -> list[dict]:
    return [{"key": _RPI5_TAINT_KEY, "operator": "Exists", "effect": "NoSchedule"}]


def emit_image_gate(
    scope: Construct,
    *,
    name: str,
    namespace: str,
    labels: dict,
    image_ref: str,
) -> None:
    """Argo PreSync hook asserting `image_ref` exists in the registry before the
    sync reaches the Deployment.

    OBS deploys with strategy Recreate (one Wayland/VNC owner), so a sync to a
    not-yet-built tag tears the live pod down first and leaves its replacement in
    ImagePullBackOff — a stream outage. PreSync hooks must succeed before the main
    sync wave, so a `crane manifest` that 404s fails the hook, aborts the sync,
    and leaves the running pod untouched. Re-sync once the image build lands. Only
    emitted for pinned (immutable-tag) envs — floating tags always resolve to a
    prior build, so they can't hit this.
    """
    _obj(
        scope,
        "image-gate",
        api_version="batch/v1",
        kind="Job",
        name=f"{name}-image-gate",
        namespace=namespace,
        labels=labels,
        annotations={
            "argocd.argoproj.io/hook": "PreSync",
            # Keep the last gate visible for debugging; replaced on next sync.
            "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        },
        spec={
            "backoffLimit": 2,
            # Cap the wait so a wedged/unschedulable probe fails the sync (pod
            # safe) instead of stalling PreSync forever.
            "activeDeadlineSeconds": 120,
            # Kubernetes never reaps finished Job pods, so gates accumulate as
            # Completed/Failed clutter in every pod-health read. A day is long
            # enough to read a failed gate's log the morning after.
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/arch": "amd64"},
                    # The `restricted` PodSecurity profile these namespaces run
                    # requires runAsNonRoot as a spec field, whatever USER the
                    # image declares — without it the gate is a violation, which
                    # would fail PreSync for a reason unrelated to the image.
                    # 65532 is crane's own default uid; it only reads the
                    # registry, so the uid is free to state explicitly.
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "image-gate",
                            "image": CRANE_IMAGE,
                            "args": ["manifest", image_ref],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "32Mi"},
                                "limits": {"memory": "64Mi"},
                            },
                        }
                    ],
                },
            },
        },
    )


def emit_volume_gate(
    scope: Construct,
    *,
    name: str,
    namespace: str,
    labels: dict,
    claim_name: str,
) -> None:
    """Argo PreSync hook asserting `claim_name` can actually be mounted before
    the sync reaches the Deployment.

    Same failure shape the image gate guards, via a different dependency. OBS
    deploys with strategy Recreate (one Wayland/VNC owner), so a sync tears the
    live pod down first — and if the claim is unbound, its replacement sits
    Pending on "unbound immediate PersistentVolumeClaims" and the stream is off
    the air. That is not hypothetical: it happened in prod on 2026-07-29, when
    the obs-music claim shipped before its out-of-band PV was provisioned.

    Kubernetes has no optional PVC (`optional` covers ConfigMap and Secret
    volumes only), so the mount is unavoidably a hard scheduling dependency.
    What this can do is move the failure off the critical path: the gate pod
    mounts the claim and runs `true`, so an unbound claim leaves the GATE pod
    Pending instead, activeDeadlineSeconds fails the hook, and the sync aborts
    with the running OBS untouched. Re-sync once the claim exists — for the music
    volume that means the infra repo's data/supporting unit has synced.

    Mounting is the whole assertion — no kubectl, no RBAC, no reading cluster
    state. The question "can a pod mount this?" is answered by trying.
    """
    _obj(
        scope,
        "volume-gate",
        api_version="batch/v1",
        kind="Job",
        name=f"{name}-volume-gate",
        namespace=namespace,
        labels=labels,
        annotations={
            "argocd.argoproj.io/hook": "PreSync",
            "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        },
        spec={
            "backoffLimit": 2,
            # An unbound claim never schedules, so the deadline IS the failure
            # signal — keep it short enough that a genuine miss fails fast.
            "activeDeadlineSeconds": 120,
            # Kubernetes never reaps finished Job pods, so gates accumulate as
            # Completed/Failed clutter in every pod-health read. A day is long
            # enough to read a failed gate's log the morning after.
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    # Pinned like the image gate: the claim is ReadOnlyMany NFS
                    # and mounts from any node, so arch is free to choose — and
                    # choosing amd64 keeps a gate failure meaning "the volume is
                    # not mountable" rather than "the image had no arm64 layer".
                    "nodeSelector": {"kubernetes.io/arch": "amd64"},
                    # The ubuntu mirror's default user is root, and the
                    # `restricted` PodSecurity profile these namespaces run
                    # requires runAsNonRoot be set explicitly — without it the
                    # gate is a violation, which would fail PreSync for a reason
                    # unrelated to the volume. Nothing is read through the mount
                    # (the kubelet does the mounting; the container only has to
                    # schedule), so any uid works.
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "volume-gate",
                            "image": VOLUME_GATE_IMAGE,
                            "command": ["true"],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {
                                    "name": "gated",
                                    "mountPath": "/gated",
                                    "readOnly": True,
                                }
                            ],
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "16Mi"},
                                "limits": {"memory": "32Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "gated",
                            "persistentVolumeClaim": {
                                "claimName": claim_name,
                                "readOnly": True,
                            },
                        }
                    ],
                },
            },
        },
    )


def _onscreens_wait_container(
    platform: str, *, image_ref: str, pull_policy: str
) -> dict:
    """InitContainer holding OBS back until its onscreens backend serves
    /health/ready.

    OBS's CEF browser sources fetch the overlays once, on first paint. Paint
    against an unready onscreens-server and CEF caches the blank result; the FPS
    cap keeps it from repainting, so the overlays stay blank until something
    reloads them — up to an hour later. Waiting for the backend costs a few
    seconds of startup and removes the race entirely.

    Runs the OBS image itself: it is already being pulled for this pod and
    already ships curl, so there is no second base image to keep current.

    The wait is bounded, and a timeout starts OBS anyway. The failure this
    guards is a startup race — onscreens coming up seconds behind OBS — and for
    a 24/7 broadcast, OBS live with blank overlays beats OBS not live at all.
    """
    url = f"{contract.onscreens_url_base(platform)}/health/ready"
    return {
        "name": "wait-onscreens",
        "image": image_ref,
        "imagePullPolicy": pull_policy,
        "command": ["sh", "-c"],
        "args": [
            f"for _ in $(seq {_ONSCREENS_WAIT_ATTEMPTS}); do "
            f'curl -fsS -o /dev/null "{url}" && exit 0; '
            f"sleep {_ONSCREENS_WAIT_INTERVAL}; done; "
            f"echo 'onscreens not ready, starting OBS anyway' >&2"
        ],
        # The pod's securityContext carries only the seccomp profile (OBS itself
        # needs root), so the restricted-profile fields ride on this container.
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "runAsUser": 65532,
        },
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"memory": "64Mi"},
        },
    }


class ObsInstance(Construct):
    def __init__(
        self,
        scope: Construct,
        platform: str,  # "twitch" | "youtube"
        *,
        env: EnvConfig,
        streaming: bool = False,  # emit the stream-key ExternalSecret
        stream_key_sm: str | None = None,  # SM path, e.g. k8s/obs/twitch-stream-key
        extra_config: dict[str, str] | None = None,
    ):
        name = f"obs-{platform}"
        super().__init__(scope, name)
        ns = env.namespace

        labels = {
            "app": name,
            "app.kubernetes.io/name": "obs",
            "app.kubernetes.io/instance": name,
            "app.kubernetes.io/part-of": PART_OF,
        }

        # --- ConfigMap ---
        data = {
            "DASHCAM_RTSP_URL": contract.dashcam_rtsp_url(platform),
            "ONSCREENS_URL_BASE": contract.onscreens_url_base(platform),
            "OBS_WEBSOCKET_PASSWD": "adanalife",
            "OBS_QUALITY_PRESET": env.obs_quality,
            "OBS_STREAM_ENCODER": env.obs_encoder,
            **(
                {"OBS_VIDEO_BITRATE": str(env.obs_video_bitrate_kbps[platform])}
                if platform in env.obs_video_bitrate_kbps
                else {}
            ),
            **(
                {"OBS_FPS_COMMON": str(env.obs_fps[platform])}
                if platform in env.obs_fps
                else {}
            ),
            # Which bed the "Background Audio" source starts on. Left unset the
            # entrypoint picks its own per-platform default, so an env that
            # doesn't care renders exactly as before.
            **(
                {"OBS_BACKGROUND_AUDIO": env.obs_background_audio[platform]}
                if platform in env.obs_background_audio
                else {}
            ),
            **(extra_config or {}),
        }
        cm_name = f"{name}-config"
        _obj(
            self,
            "config",
            api_version="v1",
            kind="ConfigMap",
            name=cm_name,
            namespace=ns,
            labels=labels,
            data=data,
        )
        cfg_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest()[:10]

        # --- stream-key ExternalSecret (streaming toggle) ---
        # twitch keeps the shared base name `obs-stream-key`; youtube gets a
        # distinct name so a twitch stream:on can't leak its key into youtube.
        secret_name = "obs-stream-key" if platform == "twitch" else f"{name}-stream-key"
        if streaming and stream_key_sm:
            secret_data = [
                {"secretKey": "STREAM_KEY", "remoteRef": {"key": stream_key_sm}},
            ]
            # TikTok has no static ingest host: Streamlabs mints the RTMP server
            # URL per session alongside the key, so the server rides in the same
            # secret (envFrom → OBS_STREAM_SERVER) instead of being baked into
            # entrypoint.sh like youtube/facebook. The built-in-service platforms
            # resolve their server by name and don't need this.
            if platform == "tiktok":
                secret_data.append(
                    {
                        "secretKey": "OBS_STREAM_SERVER",
                        "remoteRef": {"key": f"/k8s/obs/{platform}-stream-server"},
                    }
                )
            _obj(
                self,
                "stream-key",
                api_version="external-secrets.io/v1",
                kind="ExternalSecret",
                name=secret_name,
                namespace=ns,
                spec={
                    "refreshInterval": "1h",
                    "secretStoreRef": {
                        "name": "aws-parameterstore",
                        "kind": "SecretStore",
                    },
                    "target": {"name": secret_name, "creationPolicy": "Owner"},
                    "data": secret_data,
                },
            )

        # --- resources (+ iGPU claim on GPU envs) ---
        # The CPU request is the CFS weight under contention — prod sizes it for
        # real so co-tenant bursts can't starve the encoder.
        requests: dict[str, str] = {"cpu": env.obs_cpu_request, "memory": "512Mi"}
        limits: dict[str, str] = {"memory": "3Gi"}
        obs_uses_gpu = env.gpu and env.obs_gpu
        if obs_uses_gpu:
            requests["gpu.intel.com/i915"] = "1"
            limits["gpu.intel.com/i915"] = "1"

        image_ref = f"{IMAGE}:{env.tag_for('obs')}"
        container = {
            "name": "obs",
            "image": image_ref,
            "imagePullPolicy": env.pull_policy_for("obs"),
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
            "ports": [{"name": n, "containerPort": p} for n, p in contract.PORTS],
            "envFrom": [
                {"configMapRef": {"name": cm_name}},
                # optional so the pod boots idle (VNC-only) when the Secret is absent.
                {"secretRef": {"name": secret_name, "optional": True}},
            ],
            "livenessProbe": {
                "exec": {"command": ["/opt/obs/healthcheck.sh"]},
                "initialDelaySeconds": 15,
                "periodSeconds": 30,
                "timeoutSeconds": 10,
                "failureThreshold": 3,
            },
            "resources": {"requests": requests, "limits": limits},
        }

        # Recreate: one Wayland/VNC owner, no overlapping handoff.
        pod_spec: dict = {
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "initContainers": [
                _onscreens_wait_container(
                    platform,
                    image_ref=image_ref,
                    pull_policy=env.pull_policy_for("obs"),
                )
            ],
            "containers": [container],
        }

        # --- background-music share (album bed) ---
        # The node-local `obs-music-local` claim the infra repo provisions, mounted
        # read-only at the path entrypoint.sh scans for tracks. Node-local and not
        # the NAS on purpose: OBS plays the bed and composites the video in one
        # process, so a share that stops answering blocks the render pipeline and
        # takes the stream down with it — see the storage rule in the infra repo's
        # cdk8s/adanalife_k8s/constructs/music.py. Every platform's OBS mounts the
        # same claim, all on the one node that local-path volume lives on.
        # Absent on k3d/local, where the album bed degrades to carhum.
        if env.music_share:
            container["volumeMounts"] = [
                {
                    "name": "music",
                    "mountPath": "/opt/tripbot/assets/music",
                    "readOnly": True,
                }
            ]
            pod_spec["volumes"] = [
                {
                    "name": "music",
                    "persistentVolumeClaim": {
                        "claimName": MUSIC_CLAIM,
                        "readOnly": True,
                    },
                }
            ]
        if env.priority_class:
            pod_spec["priorityClassName"] = env.priority_class
        # OBS joins the rpi5 worker ONLY as a software encoder (no iGPU claim);
        # the Pi 5 has no H.264 hw encoder, so a VAAPI OBS stays on the MS-01.
        if env.prefer_rpi5 and not obs_uses_gpu:
            pod_spec["affinity"] = _prefer_rpi5_affinity()
            pod_spec["tolerations"] = _prefer_rpi5_tolerations()

        deployment_spec: dict = {
            # Births parked; a console scale-up brings the platform live and Argo
            # ignores .spec.replicas so the scale sticks (infra argocd
            # ignore_replicas). Replica count is runtime-owned, not git-owned.
            "replicas": 0,
            "selector": {"matchLabels": {"app": name}},
            "strategy": {"type": "Recreate"},
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": {CONFIG_HASH_ANNOTATION: cfg_hash},
                },
                "spec": pod_spec,
            },
        }

        _obj(
            self,
            "deployment",
            api_version="apps/v1",
            kind="Deployment",
            name=name,
            namespace=ns,
            labels=labels,
            spec=deployment_spec,
        )

        # Guard the Recreate teardown against a not-yet-built image (pinned
        # envs only — floating tags always resolve to a prior build).
        if env.is_pinned("obs"):
            emit_image_gate(
                self,
                name=name,
                namespace=ns,
                labels=labels,
                image_ref=image_ref,
            )

        # Guard the same teardown against the music claim not being mountable.
        # Not gated on is_pinned: an unbound claim strands a floating-tag env
        # just as hard, since the tag isn't what's missing.
        if env.music_share:
            emit_volume_gate(
                self,
                name=name,
                namespace=ns,
                labels=labels,
                claim_name=MUSIC_CLAIM,
            )

        # --- Service ---
        _obj(
            self,
            "service",
            api_version="v1",
            kind="Service",
            name=name,
            namespace=ns,
            labels=labels,
            spec={
                "type": "ClusterIP",
                "selector": {"app": name},
                "ports": [
                    {"name": n, "port": p, "targetPort": n} for n, p in contract.PORTS
                ],
            },
        )

        # --- host-access LoadBalancer (k3d/local convenience; no metadata
        # labels) ---
        if env.cluster in ("local", "k3d"):
            _obj(
                self,
                "host-access",
                api_version="v1",
                kind="Service",
                name=f"{name}-host",
                namespace=ns,
                spec={
                    "type": "LoadBalancer",
                    "selector": {"app": name},
                    "ports": [{"name": "vnc", "port": 5902, "targetPort": "vnc"}],
                },
            )

        # --- Ingress (noVNC) — only where the env publishes DNS (no labels) ---
        if env.dns_base:
            self._ingress(name, env, ns)
        if env.tailscale and env.dns_base:
            self._tailscale_ingress(name, env, ns)

    def _ingress(self, name: str, env: EnvConfig, ns: str):
        host = f"{name}.{env.dns_base}"
        ann = {"external-dns.alpha.kubernetes.io/hostname": host}
        # minipc envs (prod/stage) get real TLS via the namespaced Route53 issuer;
        # dev is HTTP-only.
        tls = env.cluster == "minipc"
        if tls:
            ann["cert-manager.io/issuer"] = "letsencrypt-route53"
        spec: dict = {
            "ingressClassName": "traefik",
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": name,
                                        "port": {"name": "novnc"},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        }
        if tls:
            spec["tls"] = [{"hosts": [host], "secretName": f"{name}-tls"}]
        _obj(
            self,
            "ingress",
            api_version="networking.k8s.io/v1",
            kind="Ingress",
            name=name,
            namespace=ns,
            annotations=ann,
            spec=spec,
        )

    def _tailscale_ingress(self, name: str, env: EnvConfig, ns: str):
        short = env.dns_base.split(".")[0]  # prod / stage / dev
        _obj(
            self,
            "ts-ingress",
            api_version="networking.k8s.io/v1",
            kind="Ingress",
            name=f"{name}-ts",
            namespace=ns,
            # Served by the cluster's shared HA Tailscale proxy fleet rather than
            # a dedicated proxy pod per Ingress. The *.ts.net hostname is the
            # same either way.
            annotations={"tailscale.com/proxy-group": "ingress-proxies"},
            spec={
                "ingressClassName": "tailscale",
                "defaultBackend": {
                    "service": {
                        "name": name,
                        "port": {"number": contract.NOVNC_PORT},
                    }
                },
                "tls": [{"hosts": [f"{name}-{short}"]}],
            },
        )
