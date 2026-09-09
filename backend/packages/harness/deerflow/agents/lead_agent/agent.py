"""Lead agent factory.

INVARIANT — tracing callback placement
======================================

Tracing callbacks (Langfuse, LangSmith) are attached at the **graph
invocation root** in :func:`_make_lead_agent` (see the
``build_tracing_callbacks()`` block that appends to ``config["callbacks"]``).
Every ``create_chat_model(...)`` call inside this module — and inside any
middleware reachable from this graph (e.g. ``TitleMiddleware``) — MUST pass
``attach_tracing=False``.

Forgetting that flag emits duplicate spans (one rooted at the graph, one at
the model) AND prevents the Langfuse handler's ``propagate_attributes``
path from firing, so ``session_id`` / ``user_id`` never reach the trace.
The four current sites are: bootstrap agent, default agent, summarization
middleware, and the async path inside ``TitleMiddleware``. Any new in-graph
``create_chat_model`` call must add to this list and pass the flag.
"""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.memory.summarization_hook import memory_flush_hook
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.observe_adjust_middleware import ObserveAdjustMiddleware
from deerflow.agents.middlewares.preflight_quota_middleware import PreflightQuotaMiddleware
from deerflow.agents.middlewares.reflect_fix_middleware import ReflectFixBudgetMiddleware
from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware
from deerflow.agents.middlewares.strip_error_fallback_middleware import StripErrorFallbackMiddleware
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.summarization_middleware import BeforeSummarizationHook, DeerFlowSummarizationMiddleware
from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.models import create_chat_model
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
from deerflow.skills.types import Skill
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)

_BOOTSTRAP_SKILL_NAMES = {"bootstrap"}


def _get_runtime_config(config: RunnableConfig) -> dict:
    """Merge legacy configurable options with LangGraph runtime context."""
    cfg = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        cfg.update(context)
    return cfg


def _resolve_model_name(requested_model_name: str | None = None, *, app_config: AppConfig | None = None) -> str:
    """Resolve a runtime model name safely, falling back to default if invalid. Returns None if no models are configured."""
    app_config = app_config or get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware(*, app_config: AppConfig | None = None) -> DeerFlowSummarizationMiddleware | None:
    """Create and configure the summarization middleware from config."""
    resolved_app_config = app_config or get_app_config()
    config = resolved_app_config.summarization

    if not config.enabled:
        return None

    # Prepare trigger parameter
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # Prepare keep parameter
    keep = config.keep.to_tuple()

    # Prepare model parameter.
    # Bind "middleware:summarize" tag so RunJournal identifies these LLM calls
    # as middleware rather than lead_agent (SummarizationMiddleware is a
    # LangChain built-in, so we tag the model at creation time).
    # attach_tracing=False because the graph-level RunnableConfig (set in
    # ``_make_lead_agent``) already carries tracing callbacks; binding them
    # again at the model level would emit duplicate spans and break
    # ``session_id`` / ``user_id`` propagation.
    if config.model_name:
        model = create_chat_model(name=config.model_name, thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    else:
        model = create_chat_model(thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    model = model.with_config(tags=["middleware:summarize"])

    # Prepare kwargs
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    hooks: list[BeforeSummarizationHook] = []
    if resolved_app_config.memory.enabled:
        hooks.append(memory_flush_hook)

    # The logic below relies on two assumptions holding true: this factory is
    # the sole entry point for DeerFlowSummarizationMiddleware, and the runtime
    # config is not expected to change after startup.
    skills_container_path = resolved_app_config.skills.container_path or "/mnt/skills"

    return DeerFlowSummarizationMiddleware(
        **kwargs,
        skills_container_path=skills_container_path,
        skill_file_read_tool_names=config.skill_file_read_tool_names,
        before_summarization=hooks,
        preserve_recent_skill_count=config.preserve_recent_skill_count,
        preserve_recent_skill_tokens=config.preserve_recent_skill_tokens,
        preserve_recent_skill_tokens_per_skill=config.preserve_recent_skill_tokens_per_skill,
    )


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware:
    """Create the TodoList middleware.

    Always enabled. It used to return ``None`` unless ``is_plan_mode`` was set,
    which meant ``write_todos`` did not exist outside Plan Mode — so a long
    autonomous run, exactly where a user most wants to see progress, had no
    progress tracking at all and the UI's todo panel simply never appeared.
    That read as "the todo panel is broken" rather than "the tool was never
    bound".

    ``is_plan_mode`` is kept in the signature because callers and several tests
    still pass it, and because it stays a useful hint: in Plan Mode the user has
    explicitly asked to see a plan, so the guidance leans harder on writing one.

    The prompts are deliberately short. When the tool was Plan-Mode-only its
    ~75 lines of guidance were paid for rarely; now that it is always bound,
    both the system prompt and the tool description ride on every single
    request. The previous text said the same "3+ steps, not for trivial work"
    rule three times across the two strings, so the rule survives and the
    repetition does not.
    """
    plan_mode_hint = "\nThe user has asked for a plan, so write the todo list before starting work." if is_plan_mode else ""

    system_prompt = f"""
<todo_list_system>
Use `write_todos` to track work the user should be able to watch progress on:
3+ distinct steps, a multi-part request, or when they ask for a plan. Skip it
for anything you can just do — a short answer or an obvious one-tool task is
faster without it, and a todo list for trivial work is noise.

Keep it live while you work: mark a task `in_progress` before you start it,
`completed` the moment it is done, and never batch those updates. The list is
what the user sees, so a stale list is worse than no list.{plan_mode_hint}
</todo_list_system>
"""

    tool_description = """Create and maintain a task list for multi-step work.

Use it for 3+ distinct steps, multi-part requests, or when the user asks for a
plan. Do not use it when you can simply do the work — if a couple of tool calls
finish the job, just finish the job.

States: `pending`, `in_progress`, `completed`.

Rules:
- Mark a task `in_progress` before starting it; keep exactly one in progress
  unless steps genuinely run in parallel.
- Mark `completed` immediately on finishing. Never batch completions.
- Only mark `completed` when it is actually done. If you hit an error, a
  blocker, or a partial result, leave it `in_progress` and add a task for what
  has to be resolved — a list that claims success it did not achieve is worse
  than no list.
- Revise as you learn: add steps you discover, drop ones that stop mattering.
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# ThreadDataMiddleware must be before SandboxMiddleware to ensure thread_id is available
# UploadsMiddleware should be after ThreadDataMiddleware to access thread_id
# DanglingToolCallMiddleware patches missing ToolMessages before model sees the history
# SummarizationMiddleware should be early to reduce context before other processing
# TodoListMiddleware should be before ClarificationMiddleware to allow todo management
# TitleMiddleware generates title after first exchange
# MemoryMiddleware queues conversation for memory update (after TitleMiddleware)
# ViewImageMiddleware should be before ClarificationMiddleware to inject image details before LLM
# ToolErrorHandlingMiddleware should be before ClarificationMiddleware to convert tool exceptions to ToolMessages
# ClarificationMiddleware should be last to intercept clarification requests after model calls
def build_middlewares(
    config: RunnableConfig,
    model_name: str | None,
    agent_name: str | None = None,
    custom_middlewares: list[AgentMiddleware] | None = None,
    *,
    available_skills: set[str] | None = None,
    app_config: AppConfig | None = None,
    deferred_setup=None,
):
    """Build the lead-agent middleware chain based on runtime configuration.

    Public entry point for the lead agent's full middleware composition. Used by
    ``make_lead_agent`` and by the embedded ``DeerFlowClient`` (a lead-agent variant
    that needs the identical chain). Keep this name stable: it is imported across a
    module boundary, so renames/signature changes ripple into ``client.py``.

    Args:
        config: Runtime configuration containing configurable options like is_plan_mode.
        model_name: Resolved runtime model name; gates vision-only middleware.
        agent_name: If provided, MemoryMiddleware will use per-agent memory storage.
        custom_middlewares: Optional list of custom middlewares to inject into the chain.
        app_config: Explicit AppConfig; falls back to ``get_app_config()`` when omitted.
        deferred_setup: Optional deferred-MCP-tool setup that attaches
            ``DeferredToolFilterMiddleware`` when ``tool_search`` is enabled.

    Returns:
        List of middleware instances.
    """
    resolved_app_config = app_config or get_app_config()
    middlewares = build_lead_runtime_middlewares(app_config=resolved_app_config, lazy_init=True)

    # Always inject current date (and optionally memory) as <system-reminder> into the
    # first HumanMessage to keep the system prompt fully static for prefix-cache reuse.
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    middlewares.append(DynamicContextMiddleware(agent_name=agent_name, app_config=resolved_app_config))

    # Deterministically load a full SKILL.md when the user starts the turn with
    # /skill-name. This keeps the base system prompt metadata-only while giving
    # explicit user activation priority over model-side relevance guessing.
    from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware

    middlewares.append(SkillActivationMiddleware(available_skills=available_skills, app_config=resolved_app_config))

    # Add summarization middleware if enabled
    summarization_middleware = _create_summarization_middleware(app_config=resolved_app_config)
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # TodoList middleware. Always added -- plan mode only changes how strongly
    # the prompt pushes the agent to write the list, not whether the tool exists.
    cfg = _get_runtime_config(config)
    is_plan_mode = cfg.get("is_plan_mode", False)
    middlewares.append(_create_todo_list_middleware(is_plan_mode))

    # Add TokenUsageMiddleware when token_usage tracking is enabled
    if resolved_app_config.token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())

    # Add TitleMiddleware
    middlewares.append(TitleMiddleware(app_config=resolved_app_config))

    # Add MemoryMiddleware (after TitleMiddleware)
    middlewares.append(MemoryMiddleware(agent_name=agent_name, memory_config=resolved_app_config.memory))

    # Add ViewImageMiddleware only if the current model supports vision.
    # Use the resolved runtime model_name from make_lead_agent to avoid stale config values.
    model_config = resolved_app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # Hide deferred tool schemas from model binding until tool_search promotes them.
    # The deferred set + catalog hash come from the build-time setup (assembled
    # after tool-policy filtering); promotion is read from graph state.
    if deferred_setup is not None and deferred_setup.deferred_names:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware(deferred_setup.deferred_names, deferred_setup.catalog_hash))

    # Add SubagentLimitMiddleware to truncate excess parallel task calls
    subagent_enabled = cfg.get("subagent_enabled", False)
    if subagent_enabled:
        # Fall back to config.subagents.max_concurrent, not a bare 3. The
        # literal was repeated here, at the other call site below, and in
        # subagents/config.py, so raising the limit meant finding all three --
        # and the Agent's Computer bar read a fourth source entirely.
        max_concurrent_subagents = cfg.get(
            "max_concurrent_subagents",
            resolved_app_config.subagents.max_concurrent,
        )
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # LoopDetectionMiddleware — detect and break repetitive tool call loops
    loop_detection_config = resolved_app_config.loop_detection
    if loop_detection_config.enabled:
        middlewares.append(LoopDetectionMiddleware.from_config(loop_detection_config))

    # ObserveAdjustMiddleware — emit task_progress events after each tool cycle
    middlewares.append(ObserveAdjustMiddleware())

    # ReflectFixBudgetMiddleware — runtime-enforce the prompt's "iterate at
    # most twice" rule from <self_verify>. Counts consecutive dev_verify
    # ISSUES per run; after 2 ISSUES, injects a forced HumanMessage at the
    # next wrap_model_call telling the agent to write its final answer.
    # Non-fatal by construction (every code path wrapped in try/except).
    middlewares.append(ReflectFixBudgetMiddleware())

    # StripErrorFallbackMiddleware — drop synthetic LLM error-fallback
    # messages from the model input on the next resume. Defence in depth
    # alongside the runtime/serialization.py filter that handles the
    # UI/REST path. Non-fatal by construction.
    middlewares.append(StripErrorFallbackMiddleware())

    # PreflightQuotaMiddleware — short-circuit the LLM call when the
    # provider's quota is exhausted. Only probes OpenAI-compatible
    # providers that expose /v1/dashboard/billing/credit. Fail-open
    # on any error. Per-provider cache with 60s TTL.
    middlewares.append(PreflightQuotaMiddleware())

    # Inject custom middlewares before ClarificationMiddleware
    if custom_middlewares:
        middlewares.extend(custom_middlewares)

    # SafetyFinishReasonMiddleware — suppress tool execution when the provider
    # safety-terminated the response. Registered after custom middlewares so
    # that LangChain's reverse-order after_model dispatch runs Safety first;
    # cleared tool_calls then flow through Loop/Subagent accounting without
    # firing extra alarms. See safety_finish_reason_middleware.py docstring.
    safety_config = resolved_app_config.safety_finish_reason
    if safety_config.enabled:
        middlewares.append(SafetyFinishReasonMiddleware.from_config(safety_config))

    # ClarificationMiddleware should always be last
    middlewares.append(ClarificationMiddleware())

    # SystemMessageCoalescingMiddleware — registered after Clarification so it
    # is innermost for wrap_model_call and sees every earlier middleware's
    # injections. Folds stray SystemMessages from the message list into the
    # single leading system message; strict llama.cpp chat templates 400 on
    # system messages at position > 0 ("System message must be at the
    # beginning"), hosted providers are unaffected by the merge.
    from deerflow.agents.middlewares.system_message_coalescing_middleware import SystemMessageCoalescingMiddleware

    middlewares.append(SystemMessageCoalescingMiddleware())
    return middlewares


def _available_skill_names(agent_config, is_bootstrap: bool) -> set[str] | None:
    if is_bootstrap:
        return set(_BOOTSTRAP_SKILL_NAMES)
    if agent_config and agent_config.skills is not None:
        return set(agent_config.skills)
    return None


def skills_for_tool_policy(available_skills: set[str] | None, *, app_config: AppConfig) -> list[Skill]:
    """Skills whose ``allowed-tools`` may constrain this agent's tool binding.

    ``available_skills is None`` means the caller named no skill set -- the
    default agent. That must mean *no skill restricts the binding*, not "every
    installed skill restricts it".

    It used to mean the latter, and the consequence was severe. A skill's
    ``allowed-tools`` is scoped to that skill; but ``allowed_tool_names_for_skills``
    unions the declarations and, by design, treats any single declaration as
    switching the whole policy from allow-all to allow-listed. So one public
    skill declaring six tools amputated the *entire* agent to those six.

    That is exactly what happened. ``skills/public/security/SKILL.md`` declared
    six tools; it is enabled by default (public skills are, absent an
    ``extensions_config.json`` entry) so it loaded on every run, security task or
    not. From 2026-08-25 15:56 the lead agent bound 6 tools instead of 42, and
    ``task`` was one of the 36 dropped -- subagents were dead for five days while
    a commit 58 minutes later correctly made ``subagent_enabled`` unconditional
    and appeared to do nothing.

    Restrictions now apply only where a skill set was chosen deliberately:
    bootstrap (its narrow set is the point) and custom agents that list
    ``skills:`` in their config.
    """
    if available_skills is None:
        return []

    try:
        from deerflow.agents.lead_agent.prompt import get_enabled_skills_for_config

        skills = get_enabled_skills_for_config(app_config)
    except Exception:
        logger.exception("Failed to load skills for allowed-tools policy")
        raise

    return [skill for skill in skills if skill.name in available_skills]


def make_lead_agent(config: RunnableConfig):
    """LangGraph graph factory; keep the signature compatible with LangGraph Server."""
    runtime_config = _get_runtime_config(config)
    runtime_app_config = runtime_config.get("app_config")
    return _make_lead_agent(config, app_config=runtime_app_config or get_app_config())


def _make_lead_agent(config: RunnableConfig, *, app_config: AppConfig):
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins import setup_agent, update_agent
    from deerflow.tools.builtins.tool_search import assemble_deferred_tools

    cfg = _get_runtime_config(config)
    resolved_app_config = app_config

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", resolved_app_config.subagents.max_concurrent)
    is_bootstrap = cfg.get("is_bootstrap", False)
    agent_name = validate_agent_name(cfg.get("agent_name"))

    agent_config = load_agent_config(agent_name) if not is_bootstrap else None
    available_skills = _available_skill_names(agent_config, is_bootstrap)
    # Custom agent model from agent config (if any), or None to let _resolve_model_name pick the default
    agent_model_name = agent_config.model if agent_config and agent_config.model else None

    # Final model name resolution: request → agent config → global default, with fallback for unknown names
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=resolved_app_config)

    model_config = resolved_app_config.get_model_config(model_name)

    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    # Inject run metadata for LangSmith trace tagging
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
            "tool_groups": agent_config.tool_groups if agent_config else None,
            "available_skills": sorted(available_skills) if available_skills is not None else None,
        }
    )

    # Inject tracing callbacks at the graph invocation root so a single LangGraph
    # run produces one trace with all node / LLM / tool calls as child spans,
    # AND so the Langfuse handler sees ``on_chain_start(parent_run_id=None)`` and
    # actually propagates ``langfuse_session_id`` / ``langfuse_user_id`` from
    # ``config["metadata"]`` onto the trace. Without root-level attachment the
    # model is a nested observation and the handler strips ``langfuse_*`` keys.
    tracing_callbacks = build_tracing_callbacks()
    if tracing_callbacks:
        existing = config.get("callbacks") or []
        if not isinstance(existing, list):
            existing = list(existing)
        config["callbacks"] = [*existing, *tracing_callbacks]

    restricting_skills = skills_for_tool_policy(available_skills, app_config=resolved_app_config)

    if is_bootstrap:
        # Special bootstrap agent with minimal prompt for initial custom agent creation flow
        # Keep the bootstrap skill set intentionally narrow so agent creation
        # remains deterministic before the custom agent's own config exists.
        raw_tools = get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled, app_config=resolved_app_config) + [setup_agent]
        filtered = filter_tools_by_skill_allowed_tools(raw_tools, restricting_skills)
        final_tools, setup = assemble_deferred_tools(filtered, enabled=resolved_app_config.tool_search.enabled)
        return create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, app_config=resolved_app_config, attach_tracing=False),
            tools=final_tools,
            middleware=build_middlewares(
                config,
                model_name=model_name,
                available_skills=set(_BOOTSTRAP_SKILL_NAMES),
                app_config=resolved_app_config,
                deferred_setup=setup,
            ),
            system_prompt=apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                available_skills=set(_BOOTSTRAP_SKILL_NAMES),
                app_config=resolved_app_config,
                deferred_names=setup.deferred_names,
            ),
            state_schema=ThreadState,
        )

    # Custom agents can update their own SOUL.md / config via update_agent.
    # The default agent (no agent_name) does not see this tool.
    extra_tools = [update_agent] if agent_name else []
    # Default lead agent (unchanged behavior)
    raw_tools = get_available_tools(model_name=model_name, groups=agent_config.tool_groups if agent_config else None, subagent_enabled=subagent_enabled, app_config=resolved_app_config)
    filtered = filter_tools_by_skill_allowed_tools(raw_tools + extra_tools, restricting_skills)
    final_tools, setup = assemble_deferred_tools(filtered, enabled=resolved_app_config.tool_search.enabled)
    return create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort, app_config=resolved_app_config, attach_tracing=False),
        tools=final_tools,
        middleware=build_middlewares(
            config,
            model_name=model_name,
            agent_name=agent_name,
            available_skills=available_skills,
            app_config=resolved_app_config,
            deferred_setup=setup,
        ),
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled,
            max_concurrent_subagents=max_concurrent_subagents,
            agent_name=agent_name,
            available_skills=available_skills,
            app_config=resolved_app_config,
            deferred_names=setup.deferred_names,
        ),
        state_schema=ThreadState,
    )
