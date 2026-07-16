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
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/arch": "amd64"},
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
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
                    "data": [
                        {
                            "secretKey": "STREAM_KEY",
                            "remoteRef": {"key": stream_key_sm},
                        }
                    ],
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
            "containers": [container],
        }
        if env.priority_class:
            pod_spec["priorityClassName"] = env.priority_class
        # OBS joins the rpi5 worker ONLY as a software encoder (no iGPU claim);
        # the Pi 5 has no H.264 hw encoder, so a VAAPI OBS stays on the MS-01.
        if env.prefer_rpi5 and not obs_uses_gpu:
            pod_spec["affinity"] = _prefer_rpi5_affinity()
            pod_spec["tolerations"] = _prefer_rpi5_tolerations()

        deployment_spec: dict = {
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
        replicas = env.replicas_for(platform)
        if replicas is not None:
            deployment_spec["replicas"] = replicas

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
