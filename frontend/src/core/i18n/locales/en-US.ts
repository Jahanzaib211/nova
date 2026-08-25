import {
  CompassIcon,
  GraduationCapIcon,
  ImageIcon,
  MicroscopeIcon,
  PenLineIcon,
  ShapesIcon,
  SparklesIcon,
  VideoIcon,
} from "lucide-react";

import type { Translations } from "./types";

export const enUS: Translations = {
  // Locale meta
  locale: {
    localName: "English",
  },

  // Common
  common: {
    home: "Home",
    settings: "Settings",
    delete: "Delete",
    edit: "Edit",
    rename: "Rename",
    history: "History",
    share: "Share",
    createShareLink: "Create share link",
    shareHint: "Anyone with the link can view this conversation read-only.",
    sharePlaceholder: "Creating share link…",
    copyLink: "Copy link",
    shareRevoked: "Share link revoked",
    shareFailed: "Failed to create share link",
    revoke: "Revoke",
    openInNewWindow: "Open in new window",
    close: "Close",
    more: "More",
    search: "Search",
    loadMore: "Load more",
    download: "Download",
    thinking: "Thinking",
    artifacts: "Artifacts",
    public: "Public",
    custom: "Custom",
    notAvailableInDemoMode: "Not available in demo mode",
    agentBusy:
      "The agent is still working — wait for it to finish, or press Stop.",
    loading: "Loading...",
    version: "Version",
    lastUpdated: "Last updated",
    code: "Code",
    preview: "Preview",
    cancel: "Cancel",
    save: "Save",
    install: "Install",
    create: "Create",
    import: "Import",
    export: "Export",
    exportAsMarkdown: "Export as Markdown",
    exportAsJSON: "Export as JSON",
    exportSuccess: "Conversation exported",
  },

  // Home
  home: {
    docs: "Docs",
    blog: "Blog",
    menu: "Menu",
  },

  // Welcome
  welcome: {
    greeting: "Hi, I'm Nova.",
    description:
      "I'm your computer agent. Give me a task and watch my computer go to work — researching, writing and running code in my own sandbox, and shipping real apps, slides, reports, and web pages, live.",

    createYourOwnSkill: "Create Your Own Skill",
    createYourOwnSkillDescription:
      "Create your own skill to release the power of Nova. With customized skills,\nNova can help you search on the web, analyze data, and generate\n artifacts like slides, web pages and do almost anything.",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "Copy to clipboard",
    copiedToClipboard: "Copied to clipboard",
    failedToCopyToClipboard: "Failed to copy to clipboard",
    linkCopied: "Link copied to clipboard",
  },

  // Input Box
  inputBox: {
    placeholder: "How can I assist you today?",
    createSkillPrompt:
      "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
    addAttachments: "Add attachments",
    mode: "Mode",
    flashMode: "Flash",
    flashModeDescription: "Fast and efficient, but may not be accurate",
    reasoningMode: "Reasoning",
    reasoningModeDescription:
      "Reasoning before action, balance between time and accuracy",
    proMode: "Pro",
    proModeDescription:
      "Reasoning, planning and executing, get more accurate results, may take more time",
    ultraMode: "Ultra",
    ultraModeDescription:
      "Pro mode with subagents to divide work; best for complex multi-step tasks",
    reasoningEffort: "Reasoning Effort",
    reasoningEffortMinimal: "Minimal",
    reasoningEffortMinimalDescription: "Retrieval + Direct Output",
    reasoningEffortLow: "Low",
    reasoningEffortLowDescription: "Simple Logic Check + Shallow Deduction",
    reasoningEffortMedium: "Medium",
    reasoningEffortMediumDescription:
      "Multi-layer Logic Analysis + Basic Verification",
    reasoningEffortHigh: "High",
    reasoningEffortHighDescription:
      "Full-dimensional Logic Deduction + Multi-path Verification + Backward Check",
    searchModels: "Search models...",
    surpriseMe: "Surprise",
    surpriseMePrompt: "Surprise me",
    followupLoading: "Generating follow-up questions...",
    followupConfirmTitle: "Send suggestion?",
    followupConfirmDescription:
      "You already have text in the input. Choose how to send it.",
    followupConfirmAppend: "Append & send",
    followupConfirmReplace: "Replace & send",
    suggestions: [
      {
        suggestion: "Write",
        prompt: "Write a blog post about the latest trends on [topic]",
        icon: PenLineIcon,
      },
      {
        suggestion: "Research",
        prompt:
          "Conduct a deep dive research on [topic], and summarize the findings.",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "Collect",
        prompt: "Collect data from [source] and create a report.",
        icon: ShapesIcon,
      },
      {
        suggestion: "Learn",
        prompt: "Learn about [topic] and create a tutorial.",
        icon: GraduationCapIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "Webpage",
        prompt: "Create a webpage about [topic]",
        icon: CompassIcon,
      },
      {
        suggestion: "Image",
        prompt: "Create an image about [topic]",
        icon: ImageIcon,
      },
      {
        suggestion: "Video",
        prompt: "Create a video about [topic]",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "Skill",
        prompt:
          "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar
  sidebar: {
    newChat: "New chat",
    chats: "Chats",
    channels: "Channels",
    recentChats: "Recent chats",
    demoChats: "Demo chats",
    agents: "Agents",
  },

  // Agents
  agents: {
    title: "Agents",
    description:
      "Create and manage custom agents with specialized prompts and capabilities.",
    newAgent: "New Agent",
    emptyTitle: "No custom agents yet",
    emptyDescription:
      "Create your first custom agent with a specialized system prompt.",
    apiDisabledTitle: "Agent management is turned off",
    apiDisabledDescription:
      "Custom-agent management is disabled on this server. Enable agents_api.enabled in config.yaml to create and manage agents here.",
    loadErrorTitle: "Couldn't load agents",
    loadErrorDescription:
      "Something went wrong reaching the Nova backend. Check the gateway and try again.",
    retry: "Try again",
    chat: "Chat",
    delete: "Delete",
    deleteConfirm:
      "Are you sure you want to delete this agent? This action cannot be undone.",
    deleteSuccess: "Agent deleted",
    newChat: "New chat",
    createPageTitle: "Design your Agent",
    createPageSubtitle:
      "Describe the agent you want — I'll help you create it through conversation.",
    nameStepTitle: "Name your new Agent",
    nameStepHint:
      "Letters, digits, and hyphens only — stored lowercase (e.g. code-reviewer)",
    nameStepPlaceholder: "e.g. code-reviewer",
    nameStepContinue: "Continue",
    nameStepInvalidError:
      "Invalid name — use only letters, digits, and hyphens",
    nameStepAlreadyExistsError: "An agent with this name already exists",
    nameStepNetworkError:
      "Network request failed — check your network or backend connection",
    nameStepCheckError: "Could not verify name availability — please try again",
    nameStepCheckErrorWithDetail: "Name check failed: {detail}",
    nameStepApiDisabledError:
      "Custom agent management is not enabled on this server. Please contact your administrator.",
    nameStepBootstrapMessage:
      "The new custom agent name is {name}. Help me design its purpose, behavior, and SOUL.md before saving it.",
    save: "Save agent",
    saving: "Saving agent...",
    saveRequested:
      "Save requested. Nova is generating and saving an initial version now.",
    saveHint:
      "You can save this agent at any time from the top-right menu, even if this is only a first draft.",
    saveCommandMessage:
      "Please save this custom agent now based on everything we have discussed so far. Treat this as my explicit confirmation to save. If some details are still missing, make reasonable assumptions, generate a concise first SOUL.md in English, and call setup_agent immediately without asking me for more confirmation.",
    agentCreatedPendingRefresh:
      "The agent was created, but Nova could not load it yet. Please refresh this page in a moment.",
    more: "More actions",
    agentCreated: "Agent created!",
    startChatting: "Start chatting",
    backToGallery: "Back to Gallery",
    skillsDialogDescription:
      "Toggle which skills this agent loads. Off skills are never activated for this agent (deterministic). All-on = inherit every enabled skill.",
  },

  // Breadcrumb
  breadcrumb: {
    workspace: "Workspace",
    chats: "Chats",
  },

  // Workspace
  workspace: {
    officialWebsite: "Nova's official website",
    githubTooltip: "Nova on Github",
    settingsAndMore: "Settings and more",
    visitGithub: "Nova on GitHub",
    reportIssue: "Report a issue",
    contactUs: "Contact us",
    about: "About Nova",
    logout: "Log out",
    gatewayUnavailable: "Gateway is temporarily unavailable.",
    gatewayUnavailableRetrying: "Retrying in the background…",
  },

  // Conversation
  conversation: {
    noMessages: "No messages yet",
    startConversation: "Start a conversation to see messages here",
  },

  // Chats
  chats: {
    searchChats: "Search chats",
    loadMoreToSearch: "Load more to search older conversations",
    loadingMore: "Loading more...",
    loadOlderChats: "Load older chats",
  },

  // Channels
  channels: {
    title: "Channels",
    connect: "Connect",
    modify: "Modify",
    reconnect: "Reconnect",
    disconnect: "Disconnect",
    connected: "Connected",
    notConnected: "Not connected",
    pending: "Pending",
    revoked: "Disconnected",
    disabled: "Disabled",
    unconfigured: "Not configured",
    unavailable: "Channel connections are unavailable right now.",
    unavailableShort: "Unavailable",
    setupTitle: (name: string) => `Connect ${name}`,
    setupEditTitle: (name: string) => `Modify ${name}`,
    setupDescription:
      "Enter the values needed by this server process. They are not written to config.yaml.",
    saveAndConnect: "Save and connect",
    saveChanges: "Save changes",
    descriptions: {
      telegram: "Telegram direct messages through your Nova bot.",
      slack: "Slack workspace messages and mentions.",
      discord: "Discord server messages through your Nova bot.",
      feishu: "Feishu and Lark messages through your Nova app.",
      dingtalk: "DingTalk Stream Push messages through your Nova bot.",
      wechat: "WeChat iLink messages through your Nova bot.",
      wecom: "WeCom messages through your Nova AI bot.",
    },
    connectedAs: (name: string) => `Connected as ${name}.`,
  },

  // Page titles (document title)
  pages: {
    appName: "Nova",
    chats: "Chats",
    newChat: "New chat",
    untitled: "Untitled",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `${count} more step${count === 1 ? "" : "s"}`,
    lessSteps: "Less steps",
    executeCommand: "Execute command",
    presentFiles: "Present files",
    needYourHelp: "Need your help",
    useTool: (toolName: string) => `Use "${toolName}" tool`,
    searchFor: (query: string) => `Search for "${query}"`,
    searchForRelatedInfo: "Search for related information",
    searchForRelatedImages: "Search for related images",
    searchForRelatedImagesFor: (query: string) =>
      `Search for related images for "${query}"`,
    searchOnWebFor: (query: string) => `Search on the web for "${query}"`,
    viewWebPage: "View web page",
    listFolder: "List folder",
    readFile: "Read file",
    writeFile: "Write file",
    clickToViewContent: "Click to view file content",
    writeTodos: "Update to-do list",
    skillInstallTooltip: "Install skill and make it available to Nova",
    fetchMarketData: "Fetching market data",
    computeIndicators: "Computing indicators",
    backtestSignals: "Backtesting signals",
    marketDataError: "Market data unavailable",
  },

  // Subtasks
  uploads: {
    uploading: "Uploading...",
    uploadingFiles: "Uploading files, please wait...",
  },

  subtasks: {
    subtask: "Subtask",
    executing: (count: number) =>
      `Executing ${count === 1 ? "" : count + " "}subtask${count === 1 ? "" : "s in parallel"}`,
    in_progress: "Running subtask",
    completed: "Subtask completed",
    failed: "Subtask failed",
    interrupted:
      "No result recorded — the run was interrupted before this subtask reported back",
  },

  // Token Usage
  tokenUsage: {
    title: "Token Usage",
    label: "Tokens",
    input: "Input",
    output: "Output",
    total: "Total",
    view: "Display",
    unavailable:
      "No token usage yet. Usage appears only after a successful model response when the provider returns usage_metadata.",
    unavailableShort: "No usage returned",
    note: "Header totals use persisted thread usage, plus visible in-flight usage while a run is still streaming. Per-turn and debug usage come from currently visible messages only. Totals may differ from provider billing pages.",
    presets: {
      off: "Off",
      summary: "Summary",
      perTurn: "Per turn",
      debug: "Debug",
    },
    presetDescriptions: {
      off: "Hide token usage in the header and conversation.",
      summary: "Show only the current conversation total in the header.",
      perTurn:
        "Show the header total and one token summary per assistant turn.",
      debug: "Show the header total and step-level token debugging details.",
    },
    finalAnswer: "Final answer",
    stepTotal: "Step total",
    sharedAttribution: "Shared across multiple actions in this step",
    subagent: (description: string) => `Subagent: ${description}`,
    startTodo: (content: string) => `Start To-do: ${content}`,
    completeTodo: (content: string) => `Complete To-do: ${content}`,
    updateTodo: (content: string) => `Update To-do: ${content}`,
    removeTodo: (content: string) => `Remove To-do: ${content}`,
  },

  // Shortcuts
  shortcuts: {
    searchActions: "Search actions...",
    noResults: "No results found.",
    actions: "Actions",
    keyboardShortcuts: "Keyboard Shortcuts",
    keyboardShortcutsDescription:
      "Navigate Nova faster with keyboard shortcuts.",
    openCommandPalette: "Open Command Palette",
    toggleSidebar: "Toggle Sidebar",
  },

  // Settings
  settings: {
    title: "Settings",
    description: "Adjust how Nova looks and behaves for you.",
    sections: {
      account: "Account",
      appearance: "Appearance",
      channels: "Channels",
      models: "Models",
      memory: "Memory",
      runtime: "Runtime",
      tools: "Tools",
      skills: "Skills",
      notification: "Notification",
      voice: "Voice",
      about: "About",
    },
    runtime: {
      title: "Runtime",
      description:
        "Read-only view of the operator-facing config sections that affect runtime behavior. Edit config.yaml to change these.",
      unavailable:
        "Could not load runtime config. Check that the gateway is running and you are signed in.",
      editHint:
        "Changes to these values require editing config.yaml and reloading the gateway. Some sections are restart-required.",
      on: "On",
      off: "Off",
      yes: "Yes",
      no: "No",
      notSet: "Not set",
      summarization: {
        title: "Summarization",
        description:
          "Automatic conversation summarization when approaching token or message limits.",
        enabled: "Enabled",
        model: "Model",
        trigger: "Trigger",
        keep: "Keep policy",
      },
      subagents: {
        title: "Subagents",
        description: "Delegated task execution by the lead agent.",
        timeout: "Default timeout",
        maxTurns: "Default max turns",
        customAgents: "Custom agents",
      },
      guardrails: {
        title: "Guardrails",
        description:
          "Pre-tool-call authorization that can block dangerous tool invocations.",
        enabled: "Enabled",
        failClosed: "Fail closed",
        provider: "Provider",
        passport: "Passport",
      },
    },
    models: {
      title: "Models",
      description:
        "Manage the AI models available to Nova. Models from config.yaml are read-only; models you add here are stored separately and take effect immediately.",
      loadError: "Failed to load models.",
      sourceConfig: "config",
      sourceRuntime: "custom",
      addButton: "Add model",
      addLlamaCppButton: "Add local llama.cpp model",
      addOllamaButton: "Add Ollama model (via LiteLLM)",
      addFireworksButton: "Add Fireworks model (AMD MI300X)",
      addAmdCloudButton: "Add AMD Instinct model (vLLM/ROCm)",
      addTitle: "Add model",
      editTitle: "Edit model",
      formDescription:
        "Any OpenAI-compatible endpoint works: llama.cpp server, Ollama, vLLM, or a cloud provider.",
      fieldName: "Name",
      fieldDisplayName: "Display name",
      fieldProvider: "Provider",
      fieldCustomClass: "Class path",
      fieldModelId: "Model ID",
      fieldBaseUrl: "Base URL",
      fieldApiKey: "API key",
      fieldThinking: "Supports thinking",
      fieldReasoningEffort: "Supports reasoning effort",
      fieldVision: "Supports vision",
      apiKeyUnchanged: "(unchanged)",
      providerOpenAICompatible:
        "OpenAI-compatible (llama.cpp, Ollama, vLLM, OpenAI)",
      providerAnthropic: "Anthropic",
      providerCustom: "Custom class path",
      saveButton: "Save",
      testButton: "Test",
      confirmDelete: "Confirm",
      created: "Model added.",
      updated: "Model updated.",
      deleted: "Model deleted.",
    },
    memory: {
      title: "Memory",
      description:
        "Nova automatically learns from your conversations in the background. These memories help Nova understand you better and deliver a more personalized experience.",
      empty: "No memory data to display.",
      rawJson: "Raw JSON",
      exportButton: "Export memory",
      exportSuccess: "Memory exported",
      importButton: "Import memory",
      importConfirmTitle: "Import memory?",
      importConfirmDescription:
        "This will overwrite your current memory with the selected JSON backup.",
      importFileLabel: "Selected file",
      importInvalidFile:
        "Failed to read the selected memory file. Please choose a valid JSON export.",
      importSuccess: "Memory imported",
      manualFactSource: "Manual",
      addFact: "Add fact",
      addFactTitle: "Add memory fact",
      editFactTitle: "Edit memory fact",
      addFactSuccess: "Fact created",
      editFactSuccess: "Fact updated",
      clearAll: "Clear all memory",
      clearAllConfirmTitle: "Clear all memory?",
      clearAllConfirmDescription:
        "This will remove all saved summaries and facts. This action cannot be undone.",
      clearAllSuccess: "All memory cleared",
      factDeleteConfirmTitle: "Delete this fact?",
      factDeleteConfirmDescription:
        "This fact will be removed from memory immediately. This action cannot be undone.",
      factDeleteSuccess: "Fact deleted",
      factContentLabel: "Content",
      factCategoryLabel: "Category",
      factConfidenceLabel: "Confidence",
      factContentPlaceholder: "Describe the memory fact you want to save",
      factCategoryPlaceholder: "context",
      factConfidenceHint: "Use a number between 0 and 1.",
      factSave: "Save fact",
      factValidationContent: "Fact content cannot be empty.",
      factValidationConfidence: "Confidence must be a number between 0 and 1.",
      noFacts: "No saved facts yet.",
      summaryReadOnly:
        "Summary sections are read-only for now. You can currently add, edit, or delete individual facts, or clear all memory.",
      memoryFullyEmpty: "No memory saved yet.",
      factPreviewLabel: "Fact to delete",
      searchPlaceholder: "Search memory",
      filterAll: "All",
      filterFacts: "Facts",
      filterSummaries: "Summaries",
      noMatches: "No matching memory found.",
      markdown: {
        overview: "Overview",
        userContext: "User context",
        work: "Work",
        personal: "Personal",
        topOfMind: "Top of mind",
        historyBackground: "History",
        recentMonths: "Recent months",
        earlierContext: "Earlier context",
        longTermBackground: "Long-term background",
        updatedAt: "Updated at",
        facts: "Facts",
        empty: "(empty)",
        table: {
          category: "Category",
          confidence: "Confidence",
          confidenceLevel: {
            veryHigh: "Very high",
            high: "High",
            normal: "Normal",
            unknown: "Unknown",
          },
          content: "Content",
          source: "Source",
          createdAt: "CreatedAt",
          view: "View",
        },
      },
    },
    appearance: {
      themeTitle: "Theme",
      themeDescription:
        "Choose how the interface follows your device or stays fixed.",
      system: "System",
      light: "Light",
      dark: "Dark",
      systemDescription: "Match the operating system preference automatically.",
      lightDescription: "Bright palette with higher contrast for daytime.",
      darkDescription: "Dim palette that reduces glare for focus.",
      languageTitle: "Language",
      languageDescription: "Switch between languages.",
    },
    tools: {
      title: "Tools",
      description: "Manage the configuration and enabled status of MCP tools.",
      adminRequired: "Admin privileges are required to manage MCP tools.",
      empty: "No MCP tools configured.",
      addButton: "Add MCP tool",
      addTitle: "Add MCP tool",
      editTitle: "Edit MCP tool",
      formDescription:
        "Connect an MCP server over stdio (a local command) or a remote SSE/HTTP endpoint.",
      fieldName: "Name",
      fieldDescription: "Description",
      fieldType: "Transport",
      typeStdio: "Stdio (local command)",
      typeSse: "SSE",
      typeHttp: "HTTP",
      fieldCommand: "Command",
      fieldArgs: "Arguments",
      fieldArgsHint: "One argument per line",
      fieldUrl: "URL",
      fieldEnv: "Environment variables",
      fieldHeaders: "HTTP headers",
      envKeyPlaceholder: "KEY",
      envValuePlaceholder: "value",
      addEnvVar: "Add variable",
      addHeader: "Add header",
      removeRow: "Remove",
      saveButton: "Save",
      confirmDelete: "Confirm",
      created: "MCP tool added.",
      updated: "MCP tool updated.",
      deleted: "MCP tool deleted.",
      reloadCache: "Reload MCP tool cache",
      reloadCacheHint:
        "Drop the cached tool list from MCP servers so newly installed tools appear without a restart.",
      cacheReloaded: "Tool cache reloaded.",
    },
    channels: {
      title: "Channels",
      description:
        "Connect IM accounts that can send messages to Nova from outside the browser.",
      disabled:
        "Channel connections are not enabled on this server. Ask an administrator to enable channel_connections.",
    },
    skills: {
      title: "Agent Skills",
      description:
        "Manage the configuration and enabled status of the agent skills.",
      createSkill: "Create skill",
      emptyTitle: "No agent skill yet",
      emptyDescription:
        "Put your agent skill folders under the `/skills/custom` folder under the root folder of Nova.",
      emptyButton: "Create Your First Skill",
      updateError: "Failed to update skill.",
      editButton: "Edit",
      deleteButton: "Delete",
      editTitle: "Edit skill",
      deleteTitle: "Delete this skill?",
      deleteDescription:
        "This permanently deletes the skill markdown and its history. There is no undo.",
      deleteConfirm: "Delete",
      editorHint:
        "SKILL.md content. The security scanner blocks dangerous patterns and logs every edit to the skill history.",
      saveButton: "Save changes",
      historyTitle: "Revision history",
      historyEmpty: "No changes recorded yet.",
      historyAction: "Rollback",
      historyRollbackTitle: "Restore this revision?",
      historyRollbackDescription:
        "Restores the file content as it was before this saved change. The current content is kept in the history so you can restore it later.",
      historyRollbackConfirm: "Rollback",
      historyAuthor: "Author",
      loadError: "Failed to load skill.",
      notCustomError: "Only custom skills can be edited here.",
    },
    threadHistory: {
      title: "Thread history",
      description:
        "Every message and state change is stored as a checkpoint. Expand an entry to inspect its full state snapshot.",
      empty: "No checkpoints found for this thread.",
      copyId: "Copy checkpoint ID",
      loadError: "Failed to load thread history.",
    },
    notification: {
      title: "Notification",
      description:
        "Nova only sends a completion notification when the window is not active. This is especially useful for long-running tasks so you can switch to other work and get notified when done.",
      requestPermission: "Request notification permission",
      deniedHint:
        "Notification permission was denied. You can enable it in your browser's site settings to receive completion alerts.",
      testButton: "Send test notification",
      testTitle: "Nova",
      testBody: "This is a test notification.",
      notSupported: "Your browser does not support notifications.",
      disableNotification: "Disable notification",
    },
    account: {
      profileTitle: "Profile",
      email: "Email",
      role: "Role",
      creditsTitle: "Usage",
      creditsDescription: "Your daily Nova credits, measured in tokens.",
      creditsLeftToday: "left today",
      creditsUnlimited: "Unlimited",
      creditsResets: "Resets daily at midnight UTC.",
      creditsUsedToday: "used today",
      creditsRequestMore: "Request more",
      creditsRequestReason: "Why do you need more? (optional)",
      creditsRequestSend: "Send request",
      creditsRequestPending: "Request pending — we'll review it shortly.",
      creditsRequestApproved: "Your last request was approved.",
      creditsRequestSent: "Request sent.",
      referralTitle: "Invite friends, earn credits",
      referralDescription:
        "Share your link. When a friend joins, you both get bonus tokens.",
      referralYourLink: "Your invite link",
      referralCopy: "Copy",
      referralCopied: "Copied!",
      referralCount: "friends joined",
      referralBonusActive: "bonus tokens/day active",
      byokTitle: "Bring your own API key",
      byokDescription:
        "Use your own LLM provider key. Runs bill to your account and skip the daily limit.",
      byokProvider: "Provider (e.g. openai)",
      byokApiKey: "API key",
      byokSave: "Save key",
      byokRemove: "Remove key",
      byokActive: "Active — your runs use your own key, no daily limit.",
      byokSaved: "Key saved.",
      billingTitle: "Plan & billing",
      billingDescription: "Manage your Nova subscription.",
      billingCurrentPlan: "Current plan",
      billingUpgrade: "Upgrade to Nova Plus",
      billingManage: "Manage subscription",
      billingActionFailed: "That didn't go through. Please try again.",
      changeEmailTitle: "Change Email",
      changeEmailDescription: "Update the email address on your account.",
      newEmail: "New email",
      updateEmail: "Update Email",
      emailChangedSuccess: "Email updated successfully",
      invalidEmail: "Enter a valid email address",
      changePasswordTitle: "Change Password",
      changePasswordDescription: "Update your account password.",
      currentPassword: "Current password",
      newPassword: "New password",
      confirmNewPassword: "Confirm new password",
      passwordMismatch: "New passwords do not match",
      passwordTooShort: "Password must be at least 8 characters",
      passwordChangedSuccess: "Password changed successfully",
      networkError: "Network error. Please try again.",
      updating: "Updating...",
      updatePassword: "Update Password",
      signOut: "Sign Out",
    },
    acknowledge: {
      emptyTitle: "Acknowledgements",
      emptyDescription: "Credits and acknowledgements will show here.",
    },
  },

  agentComputer: {
    header: "Agent's computer",
    thinking: "is thinking",
    usingTerminal: "is using Terminal",
    usingBrowser: "is using Browser",
    usingEditor: "is using Editor",
    taskProgress: "Task progress",
    noLogs: "No output yet",
    close: "Close",
    live: "LIVE",
    moreActions: "More actions",
    pushToGithub: "Push to GitHub",
    downloadAllZip: "Download all (zip)",
    downloadActiveFile: "Download active file",
    downloadFailed: "Download failed. Try again.",
    verifyResult: {
      passed: (count: number) =>
        `Self-test passed${count ? ` · ${count} route${count === 1 ? "" : "s"}` : ""}`,
      failed: (count: number) =>
        `Self-test found issues${count ? ` · ${count} route${count === 1 ? "" : "s"} failed` : ""}`,
      consoleErrors: (count: number) =>
        `${count} console error${count === 1 ? "" : "s"}`,
    },
    llmError: {
      prefix: "Last turn failed",
      generic: "provider error",
      quota: "out of quota",
      auth: "authentication error",
      busy: "provider busy",
    },
    tabs: {
      files: "Files",
      terminal: "Terminal",
      editor: "Editor",
      browser: "Browser",
      activity: "Activity",
      review: "Review",
      // Matches the panel header ("Recon — private web access"); the button
      // said "Privacy" and the panel said "Recon", which read as two features.
      privacy: "Recon",
    },
    files: {
      empty: "Files the agent creates will appear here",
      uploadLimitsHint: "Server-configured upload limits for this thread.",
      uploadLimits: "Upload limits",
      commandsHeader: "Commands",
      repository: "Repository",
      running: (count: number) => `${count} running`,
    },
    workspace: {
      title: "Workspace",
      index: "Index",
      indexing: "Indexing…",
      reindex: "Re-index workspace",
      monorepo: "monorepo",
      projects: "projects",
      symbols: "symbols",
      commands: "commands",
      impactFooter: (count: number) =>
        `changes here affect ${count} ${count === 1 ? "command" : "commands"}`,
      indexedBanner: (
        symbols: number,
        projects: number,
        commands: number,
        language: string,
      ) =>
        `Workspace indexed — ${symbols} symbols across ${projects} ${projects === 1 ? "project" : "projects"} · ${commands} commands · ${language}`,
      kernelTitle: "Workspace kernel",
      kernelScans: "Scans",
      kernelAvgScan: "Avg scan",
      kernelCacheHits: "Cache hits",
      liveScanned: (symbols: number, ms: number) =>
        `Live scan — ${symbols} symbols in ${Math.round(ms)}ms`,
      livePlan: (steps: number, risk: string) =>
        `Plan built — ${steps} ${steps === 1 ? "step" : "steps"} · ${risk} risk`,
      liveCacheHit: "Served from cache",
      liveCacheMiss: "Cache miss — rescanning",
    },
    status: {
      writing: (filename: string, lines?: string) =>
        `is writing ${filename}${lines ? ` (${lines})` : ""}`,
      usingEditor: "is using Editor",
      editing: (filename: string) => `is editing ${filename}`,
      reading: (filename: string) => `is reading ${filename}`,
      usingTerminal: "is using Terminal",
      searchingFiles: "is searching files",
      searchingContent: "is searching content",
      delegatingToSubagent: "is delegating to subagent",
      scaffoldingProject: "is scaffolding project",
      usingBrowser: "is using Browser",
      isThinking: "is thinking",
      isIdle: "is idle",
    },
    terminal: {
      tab: "Terminal",
      stream: "Stream",
      shell: "Shell",
      interactiveTitle: "Interactive terminal",
      noOutput: "No terminal output yet",
      noOutputHint: "Agent commands appear here — switch to",
      running: "running...",
      reconnect: "Reconnect",
      shellDisconnected:
        "The interactive shell is not responding. The sandbox may have been recycled.",
      counts: (total: number, running: number) =>
        `${total} cmd${total === 1 ? "" : "s"}${running ? ` · ${running} running` : ""}`,
    },
    editor: {
      startWriting: "Start writing a file to see code live",
      fileNotWritten: "This file doesn't exist (yet)",
      emptyFile: "File is empty",
      diff: "Diff",
      file: "File",
      lines: (count: number) => `${count} lines`,
      writing: "Writing",
    },
    browser: {
      back: "Back",
      forward: "Forward",
      reload: "Reload",
      live: "live",
      compiling: "compiling\u2026",
      switchPreview: "Switch preview (multi-port)",
      selfTest: "Self-test in browser (console + screenshot)",
      watchAgentBrowser: "Watch the agent's live browser (VNC)",
      vnc: "VNC",
      desktop: "Desktop",
      mobile: "Mobile",
      openNewTab: "Open in new tab",
      downloadHtml: "Download HTML file",
      testingInBrowser: "Testing in browser\u2026",
      selfTestPassed: "\u2713 Self-test passed",
      selfTestIssues: "\u2717 Self-test found issues",
      testedPort: (port: string) => `tested :${port}`,
      livePreview: "Live preview",
      devServerCompiling:
        "Dev server compiling\u2026 preview loads automatically",
      devServerError: "The dev server hit an error and stopped.",
      retryPreview: "Retry",
      previewWillAppear: "Browser preview will appear here",
      fileMissing: (name: string) =>
        `Preview file ${name || "(none)"} is not available in this workspace.`,
      fileEmpty: (name: string) =>
        `${name || "This file"} is empty — nothing to preview yet.`,
      previewWillAppearLine2: "once the agent writes an HTML file",
      watchLiveBrowser: "Watch the agent's live browser",
      projectType: {
        react: "React / Next.js",
        python: "Python",
        markdown: "Markdown",
        code: "Code",
      },
      projectLabel: (type: string) => `${type} project`,
      switchToEditor: "Switch to Editor to see live code.",
      startLivePreview: "Start Live Preview",
      switchToEditorPrefix: "Switch to",
      switchToEditorSuffix: "to see live code.",
    },
    activity: {
      title: (count: number) =>
        `activity \u00b7 ${count} action${count === 1 ? "" : "s"}`,
      exportAuditLog: "Export full audit log (JSONL)",
      empty: "Agent actions will appear here",
    },
    review: {
      generating: "Generating\u2026",
      generationFailed: "Review failed to load",
      needsLook: "Needs a look before shipping",
      mostlyFine: "Mostly fine \u2014 a couple of checks",
      looksClean: "Looks clean",
      codeReview: "code review",
      regenerate: "Regenerate review",
      download: "Download REVIEW.md",
      noRiskyActions: "no risky actions detected",
      riskFlags: "Risk flags",
      changedFiles: "Changed files",
      detectedChecks: "Detected checks",
      noChanges: "No changes to review yet.",
      kernelVerdictTitle: "Workspace kernel verdict",
      kernelVerdictValid: "Plan valid",
      kernelVerdictInvalid: "Plan invalid",
      kernelVerdictSteps: (count: number) =>
        `${count} ${count === 1 ? "step" : "steps"}`,
    },
    privacy: {
      title: "Recon — private web access",
      sourceHealth: "Source Health",
      searxng: "SearXNG",
      pipeline: "Pipeline",
      fetchHealth: "Fetch",
      test: "Test",
      fetch: "Fetch · one page",
      fetchMany: "Fetch many · parallel",
      crawl: "Crawl · follows links",
      /* Labels and hints keyed by the tool name the server reports, so the
         panel renders whatever capabilities exist rather than a list baked
         into the component. An unknown tool falls back to its own name. */
      capabilityLabels: {
        web_search: "Search · finds pages",
        web_fetch: "Fetch · one page",
        web_fetch_many: "Fetch many · parallel",
        web_crawl: "Crawl · follows links",
      } as Record<string, string>,
      capabilityHints: {
        web_search: "finds pages",
        web_fetch: "reads one page",
        web_fetch_many: "reads several pages you name, at once",
        web_crawl: "starts at one page and follows its links",
      } as Record<string, string>,
      /* Shown where a counter has no value at all. Rendering 0 for "the
         server sent nothing" is a lie the panel used to tell in six places. */
      noData: "—",
      fetches: "Fetches",
      avgFetch: "Avg fetch",
      capabilities: "Capabilities",
      on: "on",
      off: "off",
      healthy: "healthy",
      unhealthy: "unhealthy",
      cache: "Cache",
      size: "Size",
      hitRate: "Hit Rate",
      ttl: "TTL",
      audit: "Audit",
      total: "Total",
      errors: "Errors",
      disabledTitle: "Recon is off",
      disabledBody:
        "Web search, page fetching and the audit trail are switched off for this deployment. Nothing in this panel can turn them on \u2014 the server decides, and it decided before the page loaded.",
    },
    skillLauncher: {
      runSkill: "Run a skill on this workspace",
    },
  },

  runtimeBar: {
    status: {
      healthy: "Healthy",
      healthyTitle: "Browser subsystem healthy",
      healthyBody: "All circuit breakers are CLOSED. No active degradation.",
      degraded: (count: number) => `Degraded \u00b7 ${count}`,
      degradedTitle: (count: number) =>
        `${count} circuit${count === 1 ? "" : "s"} open`,
      degradedBody:
        "Some threads have hit the failure threshold. They will auto-recover after cooldown.",
      critical: (count: number) => `Critical \u00b7 ${count}`,
      criticalTitle: (count: number) =>
        `${count} circuits open \u2014 fleet degraded`,
      criticalBody:
        "Multiple threads tripped. Check /api/health/browser for the full state.",
    },
    skills: {
      none: "no skills loaded",
      overflow: (count: number) => `+${count}`,
      overflowHint: "See Settings \u2192 Skills to manage.",
    },
    metrics: {
      tools: "tools",
      toolsDetail: "Builtin tools available to the lead agent.",
      subagents: "subagents",
      subagentsDetail:
        "Agent types the lead can delegate to — not a count of running tasks.",
      subagentsConcurrency: (n: number) =>
        `Up to ${n} delegated task runs execute concurrently.`,
      hooks: "hooks",
      hooksDetail: "Active middlewares on the LangChain agent chain.",
    },
    igino: {
      label: "Recon",
      title: "Recon — private web access",
      tooltip: (searxng: string, fetch: string, cache: string) =>
        `Search: ${searxng} \u00b7 Fetch: ${fetch} \u00b7 Cache: ${cache}`,
    },
    circuits: {
      open: (count: number) => `${count} circuit${count === 1 ? "" : "s"} open`,
      autoRecovers: "Auto-recovers after cooldown.",
    },
    offline: "Runtime status offline",
    offlineHint: "will retry automatically",
  },

  aiElements: {
    context: {
      title: "Model context usage",
      ariaLabel: "Model context usage",
      totalCost: "Total cost",
      input: "Input",
      output: "Output",
      reasoning: "Reasoning",
      cache: "Cache",
    },
    reasoning: {
      thinking: "Thinking...",
      thoughtFew: "Thought for a few seconds",
      thought: (seconds: number) => `Thought for ${seconds} seconds`,
    },
    webPreview: {
      enterUrl: "Enter URL...",
      previewTitle: "Preview",
    },
  },

  landing: {
    footer: {
      license: "Licensed under MIT License",
    },
    hero: {
      getStarted: "Get Started with 2.0",
    },
    caseStudy: {
      title: "Case Studies",
      subtitle: "See how Nova is used in the wild",
    },
    community: {
      subtitle:
        "Contribute brilliant ideas to shape the future of Nova. Collaborate, innovate, and make impacts.",
    },
    sandbox: {
      title: "Agent Runtime Environment",
    },
    skills: {
      title: "Agent Skills",
    },
    whatsNew: {
      title: "What's New in Nova 2.0",
      subtitle:
        "Nova is now evolving from a Deep Research agent into a full-stack Super Agent",
    },
    skillsAnimation: {
      agentLabel: "Nova Agent",
      loadingSkill: (skillName: string) => `Loading ${skillName}/SKILL.md...`,
      generating: (file: string) => `Generating ${file}...`,
      executing: (script: string) => `Executing ${script}`,
    },
  },

  a11y: {
    runtimeCapabilities: "Agent runtime capabilities",
    artifactPreview: "Artifact preview",
    noArtifact: "No artifact selected",
    dragResize: "Drag or scroll to resize",
    editSkills: "Edit skills",
    skillsFor: (agentName: string) => `Skills for ${agentName}`,
    chat: "Chat",
    panels: "Panels",
    artifacts: "Artifacts",
    todos: "To-dos",
    open: "OPEN",
    thinking: "Thinking...",
  },

  auth: {
    setup: {
      loading: "Loading…",
      createAdmin: "Create admin account",
      passwordMin: "Password (min. 8 characters)",
      confirmPassword: "Confirm password",
      yourEmail: "Your email",
      currentPassword: "Current password",
      newPassword: "New password",
      confirmNewPassword: "Confirm new password",
    },
    login: {
      passwordPlaceholder: "Password",
      submitLabel: "Sign in",
      setupPrompt: "First-time setup required",
    },
  },

  authPasswordReset: {
    forgotTitle: "Forgot your password?",
    forgotHint:
      "Enter your account email and we'll send you a one-time reset link.",
    email: "Email",
    sendResetLink: "Send reset link",
    sent: "Check your inbox — a reset link is on its way if the email is registered.",
    disabled:
      "Password reset is not enabled on this deployment. Contact your operator.",
    errorGeneric: "Something went wrong. Please try again.",
    backToLogin: "Back to sign in",
    resetTitle: "Choose a new password",
    resetHint: "Your reset link is single-use and expires in 30 minutes.",
    newPassword: "New password",
    confirmNewPassword: "Confirm new password",
    resetSubmit: "Update password",
    resetSuccess: "Password updated! Sign in with your new password.",
    resetLinkInvalid: "This reset link is invalid, expired, or already used.",
    passwordsDontMatch: "Passwords do not match.",
    forgotLink: "Forgot your password?",
  },

  sharePage: {
    backHome: "Back to home",
    notFound: "Share link not found or revoked",
    notFoundHint:
      "The conversation may have been revoked by its owner, or the link is incorrect.",
    messagesEmpty: "This conversation has no messages.",
  },
};
