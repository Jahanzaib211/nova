"""DeerFlow Sandbox Provisioner Service.

Dynamically creates and manages per-sandbox Pods in Kubernetes.
Each ``sandbox_id`` gets its own Pod + NodePort Service.  The backend
accesses sandboxes directly via ``{NODE_HOST}:{NodePort}``.

The provisioner connects to the host machine's Kubernetes cluster via a
mounted kubeconfig (``~/.kube/config``).  Sandbox Pods run on the host
K8s and are accessed by the backend via ``{NODE_HOST}:{NodePort}``.

Endpoints:
    POST   /api/sandboxes              — Create a sandbox Pod + Service
    DELETE /api/sandboxes/{sandbox_id} — Destroy a sandbox Pod + Service
    GET    /api/sandboxes/{sandbox_id} — Get sandbox status & URL
    GET    /api/sandboxes              — List all sandboxes
    GET    /health                     — Provisioner health check

Architecture (docker-compose-dev):
    ┌────────────┐  HTTP  ┌─────────────┐  K8s API  ┌──────────────┐
    │ remote     │ ─────▸ │ provisioner │ ────────▸ │  host K8s    │
    │ _backend   │        │ :8002       │           │  API server  │
    └────────────┘        └─────────────┘           └──────┬───────┘
                                                           │ creates
                          ┌─────────────┐           ┌──────▼───────┐
                          │   backend   │ ────────▸ │   sandbox    │
                          │             │  direct   │   Pod(s)     │
                          └─────────────┘ NodePort  └──────────────┘
"""

from __future__ import annotations

import ast
import logging
import os
import re
import time
from contextlib import asynccontextmanager

import urllib3
from fastapi import FastAPI, HTTPException, Query
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field

# Suppress only the InsecureRequestWarning from urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Configuration (all tuneable via environment variables) ───────────────

K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", "deer-flow")
SANDBOX_IMAGE = os.environ.get(
    "SANDBOX_IMAGE",
    "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest",
)
SKILLS_HOST_PATH = os.environ.get("SKILLS_HOST_PATH", "/skills")
THREADS_HOST_PATH = os.environ.get("THREADS_HOST_PATH", "/.deer-flow/threads")
SKILLS_PVC_NAME = os.environ.get("SKILLS_PVC_NAME", "")
USERDATA_PVC_NAME = os.environ.get("USERDATA_PVC_NAME", "")
SAFE_THREAD_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
SAFE_USER_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
DEFAULT_USER_ID = "default"

# Path to the kubeconfig *inside* the provisioner container.
# Typically the host's ~/.kube/config is mounted here.
KUBECONFIG_PATH = os.environ.get("KUBECONFIG_PATH", "/root/.kube/config")

# The hostname / IP that the *backend container* uses to reach NodePort
# services on the host Kubernetes node.  On Docker Desktop for macOS this
# is ``host.docker.internal``; on Linux it may be the host's LAN IP.
NODE_HOST = os.environ.get("NODE_HOST", "host.docker.internal")

# "node-port" (default): the historical Compose scenario, where the backend
# runs *outside* the cluster and must reach sandboxes via {NODE_HOST}:{NodePort}.
# "cluster-dns": the backend runs as a Pod in the *same* cluster/namespace —
# use the Service's in-cluster DNS name instead. Caught live running gateway
# and the provisioner together in-cluster: a Pod calling its own node's
# NodePort to reach another Pod on that same node ("hairpin" routing) got
# "Connection refused" even though the same URL worked fine from the host
# and the sandbox Pod was genuinely healthy — an environment-specific
# kube-proxy/CNI interaction, not a sandbox bug. Cluster DNS sidesteps
# hairpin routing entirely and is the more correct path for an in-cluster
# caller regardless.
SANDBOX_URL_MODE = os.environ.get("SANDBOX_URL_MODE", "node-port")


def join_host_path(base: str, *parts: str) -> str:
    """Join host filesystem path segments while preserving native style."""
    if not parts:
        return base

    if re.match(r"^[A-Za-z]:[\\/]", base) or base.startswith("\\\\") or "\\" in base:
        from pathlib import PureWindowsPath

        result = PureWindowsPath(base)
        for part in parts:
            result /= part
        return str(result)

    from pathlib import Path

    result = Path(base)
    for part in parts:
        result /= part
    return str(result)


# ── K8s client setup ────────────────────────────────────────────────────

core_v1: k8s_client.CoreV1Api | None = None
# Used by the read-only /api/infra/* surface: apps_v1 for Deployment
# rollout status, metrics_api for the metrics.k8s.io aggregated API
# (pod/node CPU+mem — served by metrics-server, confirmed live in this
# cluster via `kubectl top`). Both share the same ApiClient/config as
# core_v1, so they're built alongside it in _init_k8s_client() rather than
# duplicating the kubeconfig/in-cluster-config resolution logic.
apps_v1: k8s_client.AppsV1Api | None = None
metrics_api: k8s_client.CustomObjectsApi | None = None


def _init_k8s_client() -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api, k8s_client.CustomObjectsApi]:
    """Load kubeconfig from the mounted host config and return the API clients.

    Tries the mounted kubeconfig first, then falls back to in-cluster
    config (useful if the provisioner itself runs inside K8s).
    """
    if os.path.exists(KUBECONFIG_PATH):
        if os.path.isdir(KUBECONFIG_PATH):
            raise RuntimeError(
                f"KUBECONFIG_PATH points to a directory, expected a file: {KUBECONFIG_PATH}"
            )
        try:
            k8s_config.load_kube_config(config_file=KUBECONFIG_PATH)
            logger.info(f"Loaded kubeconfig from {KUBECONFIG_PATH}")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load kubeconfig from {KUBECONFIG_PATH}: {exc}"
            ) from exc
    else:
        logger.warning(
            f"Kubeconfig not found at {KUBECONFIG_PATH}; trying in-cluster config"
        )
        try:
            k8s_config.load_incluster_config()
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize Kubernetes client. "
                f"No kubeconfig at {KUBECONFIG_PATH}, and in-cluster config is unavailable: {exc}"
            ) from exc

    # When connecting from inside Docker to the host's K8s API, the
    # kubeconfig may reference ``localhost`` or ``127.0.0.1``.  We
    # optionally rewrite the server address so it reaches the host.
    k8s_api_server = os.environ.get("K8S_API_SERVER")
    if k8s_api_server:
        configuration = k8s_client.Configuration.get_default_copy()
        configuration.host = k8s_api_server
        # Self-signed certs are common for local clusters
        configuration.verify_ssl = False
        api_client = k8s_client.ApiClient(configuration)
        return (
            k8s_client.CoreV1Api(api_client),
            k8s_client.AppsV1Api(api_client),
            k8s_client.CustomObjectsApi(api_client),
        )

    return k8s_client.CoreV1Api(), k8s_client.AppsV1Api(), k8s_client.CustomObjectsApi()


def _wait_for_kubeconfig(timeout: int = 30) -> None:
    """Wait for kubeconfig file if configured, then continue with fallback support."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(KUBECONFIG_PATH):
            if os.path.isfile(KUBECONFIG_PATH):
                logger.info(f"Found kubeconfig file at {KUBECONFIG_PATH}")
                return
            if os.path.isdir(KUBECONFIG_PATH):
                raise RuntimeError(
                    "Kubeconfig path is a directory. "
                    f"Please mount a kubeconfig file at {KUBECONFIG_PATH}."
                )
            raise RuntimeError(
                f"Kubeconfig path exists but is not a regular file: {KUBECONFIG_PATH}"
            )
        logger.info(f"Waiting for kubeconfig at {KUBECONFIG_PATH} …")
        time.sleep(2)
    logger.warning(
        f"Kubeconfig not found at {KUBECONFIG_PATH} after {timeout}s; "
        "will attempt in-cluster Kubernetes config"
    )


def _ensure_namespace() -> None:
    """Create the K8s namespace if it does not yet exist."""
    try:
        core_v1.read_namespace(K8S_NAMESPACE)
        logger.info(f"Namespace '{K8S_NAMESPACE}' already exists")
    except ApiException as exc:
        if exc.status == 404:
            ns = k8s_client.V1Namespace(
                metadata=k8s_client.V1ObjectMeta(
                    name=K8S_NAMESPACE,
                    labels={
                        "app.kubernetes.io/name": "deer-flow",
                        "app.kubernetes.io/component": "sandbox",
                    },
                )
            )
            core_v1.create_namespace(ns)
            logger.info(f"Created namespace '{K8S_NAMESPACE}'")
        else:
            raise


# ── FastAPI lifespan ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global core_v1, apps_v1, metrics_api
    _wait_for_kubeconfig()
    core_v1, apps_v1, metrics_api = _init_k8s_client()
    _ensure_namespace()
    logger.info("Provisioner is ready (using host Kubernetes)")
    yield


app = FastAPI(title="DeerFlow Sandbox Provisioner", lifespan=lifespan)


# ── Request / Response models ───────────────────────────────────────────


class CreateSandboxRequest(BaseModel):
    sandbox_id: str
    thread_id: str = Field(pattern=SAFE_THREAD_ID_PATTERN)
    user_id: str = Field(default=DEFAULT_USER_ID, pattern=SAFE_USER_ID_PATTERN)


class SandboxResponse(BaseModel):
    sandbox_id: str
    sandbox_url: str  # Direct access URL, e.g. http://host.docker.internal:{NodePort}
    status: str


# ── /api/infra/* models (read-only cluster visibility for nova-ops) ──────
# Resource quantities (cpu/memory) are returned as raw K8s strings (e.g.
# "5m", "128Mi") rather than parsed into numbers — the Quantity format has
# many suffixes (m, Ki/Mi/Gi, n, ...) and the raw string is already
# human-readable; parsing it here would be a second place to get it wrong.


class PodInfo(BaseModel):
    name: str
    phase: str
    ready: str  # "N/M" containers ready
    restarts: int
    created_at: str | None  # ISO 8601, so callers can use their own relative-time formatter
    node: str | None
    component: str | None  # app.kubernetes.io/component, or "app" label for sandbox pods


class PodsResponse(BaseModel):
    pods: list[PodInfo]
    count: int


class DeploymentInfo(BaseModel):
    name: str
    desired: int
    ready: int
    available: int
    updated: int
    conditions: list[dict[str, str | None]]


class DeploymentsResponse(BaseModel):
    deployments: list[DeploymentInfo]
    count: int


class EventInfo(BaseModel):
    type: str | None
    reason: str | None
    message: str | None
    involved_object: str
    count: int
    last_timestamp: str | None


class EventsResponse(BaseModel):
    events: list[EventInfo]
    count: int


class PodMetric(BaseModel):
    name: str
    cpu: str
    memory: str


class NodeMetric(BaseModel):
    name: str
    cpu: str
    memory: str


class MetricsResponse(BaseModel):
    pods: list[PodMetric]
    nodes: list[NodeMetric]


class PodLogsResponse(BaseModel):
    pod: str
    container: str | None
    lines: list[str]


# ── K8s resource helpers ─────────────────────────────────────────────────


def _pod_name(sandbox_id: str) -> str:
    return f"sandbox-{sandbox_id}"


def _svc_name(sandbox_id: str) -> str:
    return f"sandbox-{sandbox_id}-svc"


def _sandbox_url(sandbox_id: str, node_port: int) -> str:
    """Build the sandbox URL, per SANDBOX_URL_MODE (see its definition above)."""
    if SANDBOX_URL_MODE == "cluster-dns":
        return f"http://{_svc_name(sandbox_id)}.{K8S_NAMESPACE}.svc.cluster.local:8080"
    return f"http://{NODE_HOST}:{node_port}"


def _build_volumes(thread_id: str) -> list[k8s_client.V1Volume]:
    """Build volume list: PVC when configured, otherwise hostPath."""
    if SKILLS_PVC_NAME:
        skills_vol = k8s_client.V1Volume(
            name="skills",
            persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
                claim_name=SKILLS_PVC_NAME,
                read_only=True,
            ),
        )
    else:
        skills_vol = k8s_client.V1Volume(
            name="skills",
            host_path=k8s_client.V1HostPathVolumeSource(
                path=SKILLS_HOST_PATH,
                type="Directory",
            ),
        )

    if USERDATA_PVC_NAME:
        userdata_vol = k8s_client.V1Volume(
            name="user-data",
            persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
                claim_name=USERDATA_PVC_NAME,
            ),
        )
    else:
        userdata_vol = k8s_client.V1Volume(
            name="user-data",
            host_path=k8s_client.V1HostPathVolumeSource(
                path=join_host_path(THREADS_HOST_PATH, thread_id, "user-data"),
                type="DirectoryOrCreate",
            ),
        )

    return [skills_vol, userdata_vol]


def _build_volume_mounts(
    thread_id: str, user_id: str = DEFAULT_USER_ID
) -> list[k8s_client.V1VolumeMount]:
    """Build volume mount list, using subPath for PVC user-data."""
    userdata_mount = k8s_client.V1VolumeMount(
        name="user-data",
        mount_path="/mnt/user-data",
        read_only=False,
    )
    if USERDATA_PVC_NAME:
        userdata_mount.sub_path = (
            f"deer-flow/users/{user_id}/threads/{thread_id}/user-data"
        )

    return [
        k8s_client.V1VolumeMount(
            name="skills",
            mount_path="/mnt/skills",
            read_only=True,
        ),
        userdata_mount,
    ]


def _build_pod(
    sandbox_id: str, thread_id: str, user_id: str = DEFAULT_USER_ID
) -> k8s_client.V1Pod:
    """Construct a Pod manifest for a single sandbox."""
    return k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=_pod_name(sandbox_id),
            namespace=K8S_NAMESPACE,
            labels={
                "app": "deer-flow-sandbox",
                "sandbox-id": sandbox_id,
                "app.kubernetes.io/name": "deer-flow",
                "app.kubernetes.io/component": "sandbox",
            },
        ),
        spec=k8s_client.V1PodSpec(
            containers=[
                k8s_client.V1Container(
                    name="sandbox",
                    image=SANDBOX_IMAGE,
                    image_pull_policy="IfNotPresent",
                    ports=[
                        k8s_client.V1ContainerPort(
                            name="http",
                            container_port=8080,
                            protocol="TCP",
                        )
                    ],
                    readiness_probe=k8s_client.V1Probe(
                        http_get=k8s_client.V1HTTPGetAction(
                            path="/v1/sandbox",
                            port=8080,
                        ),
                        initial_delay_seconds=5,
                        period_seconds=5,
                        timeout_seconds=3,
                        failure_threshold=3,
                    ),
                    liveness_probe=k8s_client.V1Probe(
                        http_get=k8s_client.V1HTTPGetAction(
                            path="/v1/sandbox",
                            port=8080,
                        ),
                        initial_delay_seconds=10,
                        period_seconds=10,
                        timeout_seconds=3,
                        failure_threshold=3,
                    ),
                    resources=k8s_client.V1ResourceRequirements(
                        requests={
                            "cpu": "100m",
                            "memory": "256Mi",
                            "ephemeral-storage": "500Mi",
                        },
                        limits={
                            "cpu": "1000m",
                            "memory": "1Gi",
                            "ephemeral-storage": "500Mi",
                        },
                    ),
                    volume_mounts=_build_volume_mounts(thread_id, user_id=user_id),
                    security_context=k8s_client.V1SecurityContext(
                        privileged=False,
                        allow_privilege_escalation=True,
                    ),
                )
            ],
            volumes=_build_volumes(thread_id),
            restart_policy="Always",
        ),
    )


def _build_service(sandbox_id: str) -> k8s_client.V1Service:
    """Construct a NodePort Service manifest (port auto-allocated by K8s)."""
    return k8s_client.V1Service(
        metadata=k8s_client.V1ObjectMeta(
            name=_svc_name(sandbox_id),
            namespace=K8S_NAMESPACE,
            labels={
                "app": "deer-flow-sandbox",
                "sandbox-id": sandbox_id,
                "app.kubernetes.io/name": "deer-flow",
                "app.kubernetes.io/component": "sandbox",
            },
        ),
        spec=k8s_client.V1ServiceSpec(
            type="NodePort",
            ports=[
                k8s_client.V1ServicePort(
                    name="http",
                    port=8080,
                    target_port=8080,
                    protocol="TCP",
                    # nodePort omitted → K8s auto-allocates from the range
                )
            ],
            selector={
                "sandbox-id": sandbox_id,
            },
        ),
    )


def _get_node_port(sandbox_id: str) -> int | None:
    """Read the K8s-allocated NodePort from the Service."""
    try:
        svc = core_v1.read_namespaced_service(_svc_name(sandbox_id), K8S_NAMESPACE)
        for port in svc.spec.ports or []:
            if port.name == "http":
                return port.node_port
    except ApiException:
        pass
    return None


def _get_pod_phase(sandbox_id: str) -> str:
    """Return the Pod phase (Pending / Running / Succeeded / Failed / Unknown)."""
    try:
        pod = core_v1.read_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
        return pod.status.phase or "Unknown"
    except ApiException:
        return "NotFound"


# ── API endpoints ────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Provisioner health check."""
    return {"status": "ok"}


@app.post("/api/sandboxes", response_model=SandboxResponse)
async def create_sandbox(req: CreateSandboxRequest):
    """Create a sandbox Pod + NodePort Service for *sandbox_id*.

    If the sandbox already exists, returns the existing information
    (idempotent).
    """
    sandbox_id = req.sandbox_id.replace("\n", "").replace("\r", "")
    thread_id = req.thread_id.replace("\n", "").replace("\r", "")
    user_id = req.user_id.replace("\n", "").replace("\r", "")

    logger.info(
        "Received request to create sandbox '%s' for thread '%s' user '%s'",
        sandbox_id,
        thread_id,
        user_id,
    )

    # ── Fast path: sandbox already exists ────────────────────────────
    existing_port = _get_node_port(sandbox_id)
    if existing_port:
        return SandboxResponse(
            sandbox_id=sandbox_id,
            sandbox_url=_sandbox_url(sandbox_id, existing_port),
            status=_get_pod_phase(sandbox_id),
        )

    # ── Create Pod ───────────────────────────────────────────────────
    try:
        core_v1.create_namespaced_pod(
            K8S_NAMESPACE, _build_pod(sandbox_id, thread_id, user_id=user_id)
        )
        logger.info("Created Pod %s", _pod_name(sandbox_id))
    except ApiException as exc:
        if exc.status != 409:  # 409 = AlreadyExists
            raise HTTPException(
                status_code=500, detail=f"Pod creation failed: {exc.reason}"
            )

    # ── Create Service ───────────────────────────────────────────────
    try:
        core_v1.create_namespaced_service(K8S_NAMESPACE, _build_service(sandbox_id))
        logger.info("Created Service %s", _svc_name(sandbox_id))
    except ApiException as exc:
        if exc.status != 409:
            # Roll back the Pod on failure
            try:
                core_v1.delete_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
            except ApiException:
                pass
            raise HTTPException(
                status_code=500, detail=f"Service creation failed: {exc.reason}"
            )

    # ── Read the auto-allocated NodePort ─────────────────────────────
    node_port: int | None = None
    for _ in range(20):
        node_port = _get_node_port(sandbox_id)
        if node_port:
            break
        time.sleep(0.5)

    if not node_port:
        raise HTTPException(
            status_code=500, detail="NodePort was not allocated in time"
        )

    return SandboxResponse(
        sandbox_id=sandbox_id,
        sandbox_url=_sandbox_url(sandbox_id, node_port),
        status=_get_pod_phase(sandbox_id),
    )


@app.delete("/api/sandboxes/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """Destroy a sandbox Pod + Service."""
    errors: list[str] = []

    # Delete Service
    try:
        core_v1.delete_namespaced_service(_svc_name(sandbox_id), K8S_NAMESPACE)
        logger.info("Deleted Service %s", _svc_name(sandbox_id))
    except ApiException as exc:
        if exc.status != 404:
            errors.append(f"service: {exc.reason}")

    # Delete Pod
    try:
        core_v1.delete_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
        logger.info("Deleted Pod %s", _pod_name(sandbox_id))
    except ApiException as exc:
        if exc.status != 404:
            errors.append(f"pod: {exc.reason}")

    if errors:
        raise HTTPException(
            status_code=500, detail=f"Partial cleanup: {', '.join(errors)}"
        )

    return {"ok": True, "sandbox_id": sandbox_id}


@app.get("/api/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str):
    """Return current status and URL for a sandbox."""
    node_port = _get_node_port(sandbox_id)
    if not node_port:
        raise HTTPException(status_code=404, detail=f"Sandbox '{sandbox_id}' not found")

    return SandboxResponse(
        sandbox_id=sandbox_id,
        sandbox_url=_sandbox_url(sandbox_id, node_port),
        status=_get_pod_phase(sandbox_id),
    )


@app.get("/api/sandboxes")
async def list_sandboxes():
    """List every sandbox currently managed in the namespace."""
    try:
        services = core_v1.list_namespaced_service(
            K8S_NAMESPACE,
            label_selector="app=deer-flow-sandbox",
        )
    except ApiException as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to list services: {exc.reason}"
        )

    sandboxes: list[SandboxResponse] = []
    for svc in services.items:
        sid = (svc.metadata.labels or {}).get("sandbox-id")
        if not sid:
            continue
        node_port = None
        for port in svc.spec.ports or []:
            if port.name == "http":
                node_port = port.node_port
                break
        if node_port:
            sandboxes.append(
                SandboxResponse(
                    sandbox_id=sid,
                    sandbox_url=_sandbox_url(sid, node_port),
                    status=_get_pod_phase(sid),
                )
            )

    return {"sandboxes": sandboxes, "count": len(sandboxes)}


# ── /api/infra/* — read-only cluster visibility for nova-ops ─────────────
# Every endpoint below is a read. None of them can mutate the cluster.
# Powers nova-ops's "Infra" dashboard via the gateway's admin proxy layer
# (backend/app/gateway/routers/admin_infra.py) — see k8s/README.md.


@app.get("/api/infra/pods", response_model=PodsResponse)
async def list_all_pods():
    """List every Pod in the namespace (not just sandboxes) for the infra dashboard."""
    try:
        pods = core_v1.list_namespaced_pod(K8S_NAMESPACE)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list pods: {exc.reason}")

    infos: list[PodInfo] = []
    for pod in pods.items:
        statuses = pod.status.container_statuses or []
        ready_count = sum(1 for s in statuses if s.ready)
        restarts = sum(s.restart_count or 0 for s in statuses)
        labels = pod.metadata.labels or {}
        component = labels.get("app.kubernetes.io/component") or labels.get("app")
        ts = pod.metadata.creation_timestamp
        infos.append(
            PodInfo(
                name=pod.metadata.name,
                phase=pod.status.phase or "Unknown",
                ready=f"{ready_count}/{len(statuses)}",
                restarts=restarts,
                created_at=ts.isoformat() if ts else None,
                node=pod.spec.node_name,
                component=component,
            )
        )
    infos.sort(key=lambda p: p.name)
    return PodsResponse(pods=infos, count=len(infos))


def _normalize_pod_log_text(raw: str) -> str:
    """Work around a real bug in the kubernetes client (confirmed live,
    v36.0.3): read_namespaced_pod_log's response deserialization assumes
    JSON and falls back to str(raw_bytes) for the plain-text log response,
    so *raw* sometimes comes back as the literal Python repr of a bytes
    object (e.g. the string ``"b'line1\\nline2\\n'"``, escaped backslash-n
    included) instead of the actual decoded text. Detect that exact shape
    and unwrap it; leave normally-decoded strings untouched.
    """
    if len(raw) >= 3 and raw[0] == "b" and raw[1] in "'\"" and raw[-1] == raw[1]:
        try:
            decoded = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
        if isinstance(decoded, bytes):
            return decoded.decode("utf-8", errors="replace")
    return raw


@app.get("/api/infra/pods/{name}/logs", response_model=PodLogsResponse)
async def get_pod_logs(
    name: str,
    tail: int = Query(200, ge=1, le=2000),
    container: str | None = Query(None),
):
    """Tail logs for a Pod. Capped at 2000 lines to keep responses bounded."""
    try:
        raw = core_v1.read_namespaced_pod_log(
            name=name,
            namespace=K8S_NAMESPACE,
            tail_lines=tail,
            container=container,
        )
    except ApiException as exc:
        raise HTTPException(status_code=exc.status or 500, detail=f"Failed to read logs for '{name}': {exc.reason}")

    return PodLogsResponse(pod=name, container=container, lines=_normalize_pod_log_text(raw).splitlines())


@app.get("/api/infra/deployments", response_model=DeploymentsResponse)
async def list_deployments():
    """List Deployments with rollout status (desired/ready/available/updated)."""
    try:
        deployments = apps_v1.list_namespaced_deployment(K8S_NAMESPACE)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list deployments: {exc.reason}")

    infos: list[DeploymentInfo] = []
    for dep in deployments.items:
        status = dep.status
        conditions = [
            {"type": c.type, "status": c.status, "message": c.message}
            for c in (status.conditions or [])
        ]
        infos.append(
            DeploymentInfo(
                name=dep.metadata.name,
                desired=dep.spec.replicas or 0,
                ready=status.ready_replicas or 0,
                available=status.available_replicas or 0,
                updated=status.updated_replicas or 0,
                conditions=conditions,
            )
        )
    infos.sort(key=lambda d: d.name)
    return DeploymentsResponse(deployments=infos, count=len(infos))


@app.get("/api/infra/events", response_model=EventsResponse)
async def list_events(limit: int = Query(100, ge=1, le=500)):
    """Recent namespace Events, newest first — the live equivalent of `kubectl get events`."""
    try:
        events = core_v1.list_namespaced_event(K8S_NAMESPACE)
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list events: {exc.reason}")

    def _sort_key(ev: k8s_client.CoreV1Event) -> str:
        ts = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
        return ts.isoformat() if ts else ""

    items = sorted(events.items, key=_sort_key, reverse=True)[:limit]
    infos = [
        EventInfo(
            type=ev.type,
            reason=ev.reason,
            message=ev.message,
            involved_object=f"{ev.involved_object.kind}/{ev.involved_object.name}",
            count=ev.count or 1,
            last_timestamp=_sort_key(ev) or None,
        )
        for ev in items
    ]
    return EventsResponse(events=infos, count=len(infos))


@app.get("/api/infra/metrics", response_model=MetricsResponse)
async def get_metrics():
    """Pod + node CPU/memory usage via the metrics.k8s.io aggregated API."""
    pod_metrics: list[PodMetric] = []
    try:
        raw_pods = metrics_api.list_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", K8S_NAMESPACE, "pods"
        )
        for item in raw_pods.get("items", []):
            containers = item.get("containers", [])
            # Sum per-container usage strings isn't meaningful across mixed
            # units, so report the first container's usage for single-
            # container pods (the common case here) and note multi-container
            # pods by name only — avoids inventing a Quantity-arithmetic parser.
            cpu = containers[0]["usage"]["cpu"] if containers else "0"
            mem = containers[0]["usage"]["memory"] if containers else "0"
            pod_metrics.append(PodMetric(name=item["metadata"]["name"], cpu=cpu, memory=mem))
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read pod metrics: {exc.reason}")

    node_metrics: list[NodeMetric] = []
    try:
        raw_nodes = metrics_api.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")
        for item in raw_nodes.get("items", []):
            usage = item.get("usage", {})
            node_metrics.append(
                NodeMetric(name=item["metadata"]["name"], cpu=usage.get("cpu", "0"), memory=usage.get("memory", "0"))
            )
    except ApiException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read node metrics: {exc.reason}")

    return MetricsResponse(pods=pod_metrics, nodes=node_metrics)
