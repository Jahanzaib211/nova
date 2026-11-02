from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """Config section for a model"""

    name: str = Field(..., description="Unique name for the model")
    display_name: str | None = Field(..., default_factory=lambda: None, description="Display name for the model")
    description: str | None = Field(..., default_factory=lambda: None, description="Description for the model")
    use: str = Field(
        ...,
        description="Class path of the model provider(e.g. langchain_openai.ChatOpenAI)",
    )
    model: str = Field(..., description="Model name")
    model_config = ConfigDict(extra="allow")
    use_responses_api: bool | None = Field(
        default=None,
        description="Whether to route OpenAI ChatOpenAI calls through the /v1/responses API",
    )
    output_version: str | None = Field(
        default=None,
        description="Structured output version for OpenAI responses content, e.g. responses/v1",
    )
    supports_thinking: bool = Field(default_factory=lambda: False, description="Whether the model supports thinking")
    supports_reasoning_effort: bool = Field(default_factory=lambda: False, description="Whether the model supports reasoning effort")
    when_thinking_enabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is enabled",
    )
    when_thinking_disabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is disabled",
    )
    supports_vision: bool = Field(default_factory=lambda: False, description="Whether the model supports vision/image inputs")
    hidden: bool = Field(
        default=False,
        description=(
            "Excluded from the frontend's quick model picker when true, without "
            "affecting availability. Still fully usable (config API, settings page, "
            "existing threads pinned to it) — this only trims the default picker "
            "down to the models an operator wants surfaced by default."
        ),
    )
    max_input_tokens: int | None = Field(
        default=None,
        description=(
            "Provider-documented context window for this model, wired onto the "
            "constructed chat model's `profile` (LangChain's `max_input_tokens` "
            "profile key) rather than passed as a raw constructor kwarg. Lets "
            "SummarizationMiddleware use a `fraction`-type trigger that scales "
            "correctly per model instead of one hand-picked token count shared "
            "across models with very different real context windows."
        ),
    )
    stream_chunk_timeout: float | None = Field(
        default=None,
        description=(
            "Maximum seconds to wait between successive streaming chunks before "
            "langchain-openai raises StreamChunkTimeoutError. None means use the "
            "factory default (240s for OpenAI-compatible clients). Tune higher for "
            "reasoning models with long thinking pauses; lower for latency-sensitive "
            "interactive endpoints. Has no effect on non-OpenAI-compatible providers."
        ),
    )
    thinking: dict | None = Field(
        default_factory=lambda: None,
        description=(
            "Thinking settings for the model. If provided, these settings will be passed to the model when thinking is enabled. "
            "This is a shortcut for `when_thinking_enabled` and will be merged with `when_thinking_enabled` if both are provided."
        ),
    )
