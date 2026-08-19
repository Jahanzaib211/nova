#!/usr/bin/env bash
set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT/docker"

# Docker Compose command with project name
COMPOSE_CMD="docker compose -p deer-flow-dev -f docker-compose-dev.yaml"

load_proxy_env_from_dotenv() {
    local env_file="$PROJECT_ROOT/.env"
    local var
    local line
    local value

    if [ ! -f "$env_file" ]; then
        return
    fi

    for var in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; do
        if [ -z "${!var+x}" ]; then
            line="$(grep -E "^[[:space:]]*${var}=" "$env_file" | tail -n 1 || true)"
            if [ -n "$line" ]; then
                value="${line#*=}"
                value="${value%\"}"
                value="${value#\"}"
                value="${value%\'}"
                value="${value#\'}"
                value="${value%$'\r'}"
                export "${var}=${value}"
            fi
        fi
    done
}

# Is `speech.enabled: true` set in config.yaml? Same awk section-walk as
# detect_sandbox_mode — deliberately not a YAML parser, since adding a Python
# dependency to a shell wrapper that runs before the venv exists is worse than
# reading one boolean the blunt way.
speech_enabled_in_config() {
    local config_file="$PROJECT_ROOT/config.yaml"
    [ -f "$config_file" ] || return 1
    awk '
        /^[[:space:]]*speech:[[:space:]]*$/ { in_speech=1; next }
        in_speech && /^[^[:space:]#]/ { in_speech=0 }
        in_speech && /^[[:space:]]*enabled:[[:space:]]*true[[:space:]]*$/ { found=1; exit }
        END { exit(found ? 0 : 1) }
    ' "$config_file"
}

detect_sandbox_mode() {
    local config_file="$PROJECT_ROOT/config.yaml"
    local sandbox_use=""
    local provisioner_url=""

    if [ ! -f "$config_file" ]; then
        echo "local"
        return
    fi

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*use:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$config_file")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$config_file")

    if [[ "$sandbox_use" == *"deerflow.sandbox.local:LocalSandboxProvider"* ]]; then
        echo "local"
    elif [[ "$sandbox_use" == *"deerflow.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

# Cleanup function for Ctrl+C
cleanup() {
    echo ""
    echo -e "${YELLOW}Operation interrupted by user${NC}"
    exit 130
}

# Set up trap for Ctrl+C
trap cleanup INT TERM

docker_available() {
    # Check that the docker CLI exists
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi

    # Check that the Docker daemon is reachable
    if ! docker info >/dev/null 2>&1; then
        return 1
    fi

    return 0
}

# Initialize: pre-pull the sandbox image so first Pod startup is fast
init() {
    echo "=========================================="
    echo "  DeerFlow Init — Pull Sandbox Image"
    echo "=========================================="
    echo ""

    SANDBOX_IMAGE="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"

    # Detect sandbox mode from config.yaml
    local sandbox_mode
    sandbox_mode="$(detect_sandbox_mode)"

    # Skip image pull for local sandbox mode (no container image needed)
    if [ "$sandbox_mode" = "local" ]; then
        echo -e "${GREEN}Detected local sandbox mode — no Docker image required.${NC}"
        echo ""

        if docker_available; then
            echo -e "${GREEN}✓ Docker environment is ready.${NC}"
            echo ""
            echo -e "${YELLOW}Next step: make docker-start${NC}"
        else
            echo -e "${YELLOW}Docker does not appear to be installed, or the Docker daemon is not reachable.${NC}"
            echo "Local sandbox mode itself does not require Docker, but Docker-based workflows (e.g., docker-start) will fail until Docker is available."
            echo ""
            echo -e "${YELLOW}Install and start Docker, then run: make docker-init && make docker-start${NC}"
        fi

        return 0
    fi

    if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${SANDBOX_IMAGE}$"; then
        echo -e "${BLUE}Pulling sandbox image: $SANDBOX_IMAGE ...${NC}"
        echo ""

        if ! docker pull "$SANDBOX_IMAGE" 2>&1; then
            echo ""
            echo -e "${YELLOW}⚠ Failed to pull sandbox image.${NC}"
            echo ""
            echo "This is expected if:"
            echo "  1. You are using local sandbox mode (default — no image needed)"
            echo "  2. You are behind a corporate proxy or firewall"
            echo "  3. The registry requires authentication"
            echo ""
            echo -e "${GREEN}The Docker development environment can still be started.${NC}"
            echo "If you need AIO sandbox (container-based execution):"
            echo "  - Ensure you have network access to the registry"
            echo "  - Or configure a custom sandbox image in config.yaml"
            echo ""
            echo -e "${YELLOW}Next step: make docker-start${NC}"
            return 0
        fi
    else
        echo -e "${GREEN}Sandbox image already exists locally: $SANDBOX_IMAGE${NC}"
    fi

    echo ""
    echo -e "${GREEN}✓ Sandbox image is ready.${NC}"
    echo ""
    echo -e "${YELLOW}Next step: make docker-start${NC}"
}

# Start Docker development environment
start() {
    local sandbox_mode
    local services

    if [ "$#" -gt 0 ]; then
        echo -e "${YELLOW}Unknown option for start: $1${NC}"
        echo "Usage: $0 start"
        exit 1
    fi

    echo "=========================================="
    echo "  Starting DeerFlow Docker Development"
    echo "=========================================="
    echo ""

    sandbox_mode="$(detect_sandbox_mode)"

    services="frontend gateway nginx"
    if [ "$sandbox_mode" = "provisioner" ]; then
        services="frontend gateway provisioner nginx"
    fi

    # Only aio mode (AioSandboxProvider without provisioner_url) needs the host
    # Docker socket. Mount it via the opt-in docker-compose.dood.yaml overlay so
    # the default (local) and provisioner modes never expose the host daemon.
    # Mounting the socket = root-equivalent host control; see SECURITY.md.
    if [ "$sandbox_mode" = "aio" ]; then
        local docker_socket="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
        if [ ! -S "$docker_socket" ]; then
            echo -e "${YELLOW}⚠ Docker socket not found at $docker_socket — AioSandboxProvider (DooD) will not work.${NC}"
            exit 1
        fi
        echo -e "${YELLOW}Mounting host Docker socket into gateway (DooD = host root-equivalent). See SECURITY.md.${NC}"
        COMPOSE_CMD="$COMPOSE_CMD -f $DOCKER_DIR/docker-compose.dood.yaml"
    fi

    # Voice needs two things the base compose file cannot assume: the model
    # weights on disk and the `voice` uv extra. Both are gated here so a machine
    # without weights starts normally and simply reports voice unavailable,
    # rather than bind-mounting an empty directory and failing at first use.
    if speech_enabled_in_config; then
        local voice_dir="${DEERFLOW_VOICE_MODEL_DIR:-$HOME/.cache/nova/voice}"
        local kokoro=""
        # fp32 first — it is the default and ~5.5x faster than int8 (measured;
        # see scripts/fetch-voice-models.sh). Fall back to int8 so a host that
        # only fetched the smaller weights still gets working voice.
        for candidate in kokoro-v1.0.onnx kokoro-v1.0.int8.onnx; do
            if [ -s "$voice_dir/$candidate" ]; then kokoro="$candidate"; break; fi
        done
        if [ -n "$kokoro" ] && [ -s "$voice_dir/voices-v1.0.bin" ] && [ -s "$voice_dir/silero_vad.onnx" ]; then
            export DEERFLOW_VOICE_MODEL_DIR="$voice_dir"
            export DEERFLOW_TTS_MODEL_FILE="$kokoro"

            # Use the GPU only when the host can actually deliver it. `nvidia-smi`
            # alone is not enough: the driver can be fine while Docker has no
            # `nvidia` runtime registered, and a device reservation then stops
            # the container from starting at all. Both must hold.
            local voice_extra="voice"
            local use_gpu=0
            if nvidia-smi -L >/dev/null 2>&1 && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then
                use_gpu=1
                voice_extra="voice-gpu"
            fi

            # `voice` and `voice-gpu` are alternatives, not additions — both
            # provide the `onnxruntime` module. Strip either before appending.
            local base_extras="${UV_EXTRAS:-trading}"
            local cleaned=""
            local part
            for part in $(printf '%s' "$base_extras" | tr ',' ' '); do
                case "$part" in
                    voice | voice-gpu | "") continue ;;
                    *) cleaned="${cleaned:+$cleaned,}$part" ;;
                esac
            done
            export DEERFLOW_VOICE_UV_EXTRAS="${cleaned:+$cleaned,}$voice_extra"

            echo -e "${BLUE}Voice enabled: mounting $voice_dir (read-only), extras=$DEERFLOW_VOICE_UV_EXTRAS${NC}"
            COMPOSE_CMD="$COMPOSE_CMD -f $DOCKER_DIR/docker-compose.voice.yaml"
            if [ "$use_gpu" = "1" ]; then
                echo -e "${BLUE}  GPU detected — granting the gateway a CUDA device (engines still fall back to CPU on their own).${NC}"
                COMPOSE_CMD="$COMPOSE_CMD -f $DOCKER_DIR/docker-compose.voice-gpu.yaml"
            else
                echo -e "${YELLOW}  No usable GPU (needs nvidia-smi + the nvidia Docker runtime) — voice will run on CPU.${NC}"
            fi
        else
            echo -e "${YELLOW}⚠ speech.enabled is true but model weights are missing in $voice_dir — voice will report unavailable.${NC}"
            echo -e "${YELLOW}  Run ./scripts/fetch-voice-models.sh to download them.${NC}"
        fi
    fi

    echo -e "${BLUE}Runtime: Gateway embedded agent runtime${NC}"
    echo -e "${BLUE}Detected sandbox mode: $sandbox_mode${NC}"
    if [ "$sandbox_mode" = "provisioner" ]; then
        echo -e "${BLUE}Provisioner enabled (Kubernetes mode).${NC}"
    else
        echo -e "${BLUE}Provisioner disabled (not required for this sandbox mode).${NC}"
    fi
    echo ""
    
    # Set DEER_FLOW_ROOT for provisioner if not already set
    if [ -z "$DEER_FLOW_ROOT" ]; then
        export DEER_FLOW_ROOT="$PROJECT_ROOT"
        echo -e "${BLUE}Setting DEER_FLOW_ROOT=$DEER_FLOW_ROOT${NC}"
        echo ""
    fi
    
    # Ensure config.yaml exists before starting.
    if [ ! -f "$PROJECT_ROOT/config.yaml" ]; then
        if [ -f "$PROJECT_ROOT/config.example.yaml" ]; then
            cp "$PROJECT_ROOT/config.example.yaml" "$PROJECT_ROOT/config.yaml"
            echo ""
            echo -e "${YELLOW}============================================================${NC}"
            echo -e "${YELLOW}  config.yaml has been created from config.example.yaml.${NC}"
            echo -e "${YELLOW}  Please edit config.yaml to set your API keys and model   ${NC}"
            echo -e "${YELLOW}  configuration before starting DeerFlow.                  ${NC}"
            echo -e "${YELLOW}============================================================${NC}"
            echo ""
            echo -e "${YELLOW}  Recommended: run 'make setup' before starting Docker.    ${NC}"
            echo -e "${YELLOW}  Edit the file:  $PROJECT_ROOT/config.yaml${NC}"
            echo -e "${YELLOW}  Then run:        make docker-start${NC}"
            echo ""
            exit 0
        else
            echo -e "${YELLOW}✗ config.yaml not found and no config.example.yaml to copy from.${NC}"
            exit 1
        fi
    fi

    # Ensure extensions_config.json exists as a file before mounting.
    # Docker creates a directory when bind-mounting a non-existent host path.
    if [ ! -f "$PROJECT_ROOT/extensions_config.json" ]; then
        if [ -f "$PROJECT_ROOT/extensions_config.example.json" ]; then
            cp "$PROJECT_ROOT/extensions_config.example.json" "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created extensions_config.json from example${NC}"
        else
            echo "{}" > "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created empty extensions_config.json${NC}"
        fi
    fi

    load_proxy_env_from_dotenv

    echo "Building and starting containers..."
    cd "$DOCKER_DIR" && $COMPOSE_CMD up --build -d --remove-orphans $services
    echo ""
    echo "=========================================="
    echo "  DeerFlow Docker is starting!"
    echo "=========================================="
    echo ""
    echo "  🌐 Application: http://localhost:2026"
    echo "  📡 API Gateway: http://localhost:2026/api/*"
    echo "  🤖 Runtime:     Gateway embedded"
    echo "  API:            /api/langgraph/* → Gateway"
    echo ""
    echo "  📋 View logs: make docker-logs"
    echo "  🛑 Stop:      make docker-stop"
    echo ""
}

# View Docker development logs
logs() {
    local service=""
    
    case "$1" in
        --frontend)
            service="frontend"
            echo -e "${BLUE}Viewing frontend logs...${NC}"
            ;;
        --gateway)
            # The dev gateway's entrypoint redirects stdout/stderr into the
            # host-mounted logs/gateway.log (docker/dev-entrypoint.sh), so
            # `compose logs gateway` streams an empty container log and looks
            # like "the gateway is silent" during an outage. Follow the real
            # file when it exists; fall back to compose logs for stacks that do
            # not redirect (docker-compose.nova-prod.yaml).
            if [ -f "$PROJECT_ROOT/logs/gateway.log" ]; then
                echo -e "${BLUE}Viewing gateway logs (logs/gateway.log)...${NC}"
                exec tail -f "$PROJECT_ROOT/logs/gateway.log"
            fi
            service="gateway"
            echo -e "${BLUE}Viewing gateway logs...${NC}"
            ;;
        --nginx)
            service="nginx"
            echo -e "${BLUE}Viewing nginx logs...${NC}"
            ;;
        --provisioner)
            service="provisioner"
            echo -e "${BLUE}Viewing provisioner logs...${NC}"
            ;;
        "")
            echo -e "${BLUE}Viewing all logs...${NC}"
            ;;
        *)
            echo -e "${YELLOW}Unknown option: $1${NC}"
            echo "Usage: $0 logs [--frontend|--gateway|--nginx|--provisioner]"
            exit 1
            ;;
    esac
    
    cd "$DOCKER_DIR" && $COMPOSE_CMD logs -f $service
}

# Stop Docker development environment
stop() {
    # DEER_FLOW_ROOT is referenced in docker-compose-dev.yaml; set it before
    # running compose down to suppress "variable is not set" warnings.
    if [ -z "$DEER_FLOW_ROOT" ]; then
        export DEER_FLOW_ROOT="$PROJECT_ROOT"
    fi
    echo "Stopping Docker development services..."
    cd "$DOCKER_DIR" && $COMPOSE_CMD down
    echo "Cleaning up sandbox containers..."
    "$SCRIPT_DIR/cleanup-containers.sh" deer-flow-sandbox 2>/dev/null || true
    echo -e "${GREEN}✓ Docker services stopped${NC}"
}

# Status: report whether the dev stack is healthy and the gateway can reach
# the host Docker socket (the AioSandboxProvider's DooD mount). This is the
# one-shot answer to "is the sandbox actually working right now?" — it catches
# the two common failure modes:
#   1. docker daemon down / socket missing on host
#   2. gateway container started without the DooD overlay, so AIO sandboxes
#      silently cannot spawn even though `docker ps` looks fine
# Run from anywhere; resolves PROJECT_ROOT itself.
status() {
    local sandbox_mode
    sandbox_mode="$(detect_sandbox_mode)"

    echo "=========================================="
    echo "  DeerFlow Docker Status"
    echo "=========================================="
    echo ""

    if ! docker_available; then
        echo -e "${YELLOW}✗ Docker CLI or daemon unavailable.${NC}"
        echo "  Install/start Docker, then re-run: $0 status"
        return 1
    fi

    echo -e "${BLUE}Sandbox mode (config.yaml): ${sandbox_mode}${NC}"

    local socket="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
    if [ -S "$socket" ]; then
        echo -e "${GREEN}✓ Host Docker socket present: ${socket}${NC}"
    else
        echo -e "${YELLOW}✗ Host Docker socket missing: ${socket}${NC}"
        echo "  Start Docker, or set DEER_FLOW_DOCKER_SOCKET to a valid path."
    fi

    local gateway_container="deer-flow-gateway"
    local state
    state="$(docker inspect -f '{{.State.Status}}' "$gateway_container" 2>/dev/null || echo absent)"
    if [ "$state" = "absent" ]; then
        echo -e "${YELLOW}✗ ${gateway_container} not running. Run: $0 start${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ ${gateway_container} container: ${state}${NC}"

    if docker exec "$gateway_container" test -S /var/run/docker.sock 2>/dev/null; then
        echo -e "${GREEN}✓ Docker socket mounted inside gateway (DooD OK).${NC}"
    else
        echo -e "${YELLOW}✗ Docker socket NOT mounted inside gateway.${NC}"
        if [ "$sandbox_mode" = "aio" ]; then
            echo "  Sandbox mode is aio but the DooD overlay is missing."
            echo "  Run: $0 start   (it will append docker-compose.dood.yaml automatically)"
        else
            echo "  Sandbox mode is ${sandbox_mode}; DooD not required by config."
        fi
        return 1
    fi

    local health
    health="$(docker inspect -f '{{.State.Health.Status}}' "$gateway_container" 2>/dev/null || echo none)"
    echo -e "${BLUE}Gateway health probe: ${health}${NC}"

    echo ""
    echo -e "${BLUE}Containers:${NC}"
    docker ps --filter "label=com.docker.compose.project=deer-flow-dev" --format "  table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null \
        | sed 's/^/  /'

    return 0
}

# Restart Docker development environment
restart() {
    echo "========================================"
    echo "  Restarting DeerFlow Docker Services"
    echo "========================================"
    echo ""
    echo -e "${BLUE}Restarting containers...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD restart
    echo ""
    echo -e "${GREEN}✓ Docker services restarted${NC}"
    echo ""
    echo "  🌐 Application: http://localhost:2026"
    echo "  📋 View logs: make docker-logs"
    echo ""
}

# Show help
help() {
    echo "DeerFlow Docker Management Script"
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  init              - Pull the sandbox image (speeds up first Pod startup)"
    echo "  start             - Start Docker services (auto-detects sandbox mode from config.yaml)"
    echo "  restart           - Restart all running Docker services"
    echo "  logs [option] - View Docker development logs"
    echo "                  --frontend   View frontend logs only"
    echo "                  --gateway    View gateway logs only"
    echo "                  --nginx      View nginx logs only"
    echo "                  --provisioner View provisioner logs only"
    echo "  stop          - Stop Docker development services"
    echo "  help          - Show this help message"
    echo ""
}

main() {
    # Main command dispatcher
    case "$1" in
        init)
            init
            ;;
        start)
            shift
            start "$@"
            ;;
        restart)
            restart
            ;;
        logs)
            logs "$2"
            ;;
        stop)
            stop
            ;;
        status)
            status
            ;;
        help|--help|-h|"")
            help
            ;;
        *)
            echo -e "${YELLOW}Unknown command: $1${NC}"
            echo ""
            help
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
