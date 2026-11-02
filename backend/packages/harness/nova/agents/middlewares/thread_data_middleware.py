import logging
from datetime import UTC, datetime
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.manifest import build_agent_manifest
from deerflow.agents.thread_state import ThreadDataState
from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

# Tag used to identify the injected manifest in the message list. Used
# to detect re-injection so the manifest fires once per thread, not once
# per turn.
_MANIFEST_TAG = "<agent_manifest>"


class ThreadDataMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """Create thread data directories for each thread execution.

    Creates the following directory structure:
    - {base_dir}/threads/{thread_id}/user-data/workspace
    - {base_dir}/threads/{thread_id}/user-data/uploads
    - {base_dir}/threads/{thread_id}/user-data/outputs

    Lifecycle Management:
    - With lazy_init=True (default): Only compute paths, directories created on-demand
    - With lazy_init=False: Eagerly create directories in before_agent()
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
            lazy_init: If True, defer directory creation until needed.
                      If False, create directories eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """Get the paths for a thread's data directories.

        Args:
            thread_id: The thread ID.
            user_id: Optional user ID for per-user path isolation.

        Returns:
            Dictionary with workspace_path, uploads_path, and outputs_path.
        """
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

    def _create_thread_directories(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """Create the thread data directories.

        Args:
            thread_id: The thread ID.
            user_id: Optional user ID for per-user path isolation.

        Returns:
            Dictionary with the created directory paths.
        """
        self._paths.ensure_thread_dirs(thread_id, user_id=user_id)
        return self._get_thread_paths(thread_id, user_id=user_id)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        context = runtime.context or {}
        thread_id = context.get("thread_id")
        if thread_id is None:
            config = get_config()
            thread_id = config.get("configurable", {}).get("thread_id")

        if thread_id is None:
            raise ValueError("Thread ID is required in runtime context or config.configurable")

        user_id = get_effective_user_id()

        if self._lazy_init:
            # Lazy initialization: only compute paths, don't create directories
            paths = self._get_thread_paths(thread_id, user_id=user_id)
        else:
            # Eager initialization: create directories immediately
            paths = self._create_thread_directories(thread_id, user_id=user_id)
            logger.debug("Created thread data directories for thread %s", thread_id)

        messages = list(state.get("messages", []))
        last_message = messages[-1] if messages else None

        if last_message and isinstance(last_message, HumanMessage):
            messages[-1] = HumanMessage(
                content=last_message.content,
                id=last_message.id,
                name=last_message.name or "user-input",
                additional_kwargs={**last_message.additional_kwargs, "run_id": runtime.context.get("run_id"), "timestamp": datetime.now(UTC).isoformat()},
            )

        messages = self._maybe_inject_manifest(messages)

        # v7.4-d2: journal thread-data initialisation for audit trail
        try:
            ctx = runtime.context if isinstance(runtime.context, dict) else {}
            journal = ctx.get("__run_journal")
            if journal is not None:
                journal.record_middleware(
                    "thread_data",
                    name="ThreadDataMiddleware",
                    hook="before_agent",
                    action="init_paths",
                    changes={"thread_id": thread_id, "lazy": self._lazy_init},
                )
        except Exception:
            pass

        return {
            "thread_data": {
                **paths,
            },
            "messages": messages,
        }

    @staticmethod
    def _maybe_inject_manifest(messages: list) -> list:
        """Prepend the agent self-knowledge manifest if not already present.

        The manifest is a deterministic block that gives the model
        canonical self-knowledge on turn 1 — sandbox, tools, gates, hard
        rules, panel layout, search discipline. Without it, the agent has
        to infer all of this from the prompt's tool menu, which is where
        the v3 bootstrap gap surfaced (e.g. the cowork-fullstack
        hallucination).

        Behaviour:
          - Fire-once-per-thread: if any existing SystemMessage already
            contains the manifest tag, do nothing (no re-injection on
            subsequent turns).
          - Non-fatal: if build_agent_manifest() raises, log at debug
            and return the messages unchanged. The run proceeds with the
            original system prompt.
          - Position: prepended so the manifest lands at the top of the
            context window. Existing messages are preserved.
        """
        # Check for an existing manifest to avoid re-injection per turn
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str) and _MANIFEST_TAG in content:
                return messages

        try:
            manifest_text = build_agent_manifest()
        except Exception as exc:  # noqa: BLE001 — injection is non-fatal
            logger.debug("manifest injection failed (%s); continuing without manifest", exc)
            return messages

        manifest_message = SystemMessage(content=manifest_text)
        return [manifest_message, *messages]
