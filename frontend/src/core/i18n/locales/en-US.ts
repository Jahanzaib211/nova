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
    share: "Share",
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
      tools: "Tools",
      skills: "Skills",
      notification: "Notification",
      about: "About",
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
    verifyResult: {
      passed: (count: number) =>
        `Self-test passed${count ? ` · ${count} route${count === 1 ? "" : "s"}` : ""}`,
      failed: (count: number) =>
        `Self-test found issues${count ? ` · ${count} route${count === 1 ? "" : "s"} failed` : ""}`,
      consoleErrors: (count: number) =>
        `${count} console error${count === 1 ? "" : "s"}`,
    },
    tabs: {
      files: "Files",
      terminal: "Terminal",
      editor: "Editor",
      browser: "Browser",
      activity: "Activity",
      review: "Review",
      privacy: "Privacy",
    },
    files: {
      empty: "Files the agent creates will appear here",
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
    },
    editor: {
      startWriting: "Start writing a file to see code live",
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
      testingInBrowser: "Testing in browser\u2026",
      selfTestPassed: "\u2713 Self-test passed",
      selfTestIssues: "\u2717 Self-test found issues",
      testedPort: (port: string) => `tested :${port}`,
      livePreview: "Live preview",
      devServerCompiling:
        "Dev server compiling\u2026 preview loads automatically",
      previewWillAppear: "Browser preview will appear here",
      fileMissing: (name: string) =>
        `Preview file ${name || "(none)"} is not available in this workspace.`,
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
    },
    privacy: {
      title: "iGIN0 Privacy Search",
      sourceHealth: "Source Health",
      searxng: "SearXNG",
      tor: "TOR",
      healthy: "healthy",
      unhealthy: "unhealthy",
      available: "available",
      unavailable: "unavailable",
      cache: "Cache",
      size: "Size",
      hitRate: "Hit Rate",
      ttl: "TTL",
      audit: "Audit",
      total: "Total",
      errors: "Errors",
      torUsage: "TOR",
      toggleLabel: "Toggle iGIN0 privacy search",
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
      subagentsDetail: "Delegated worker agents the lead can spawn.",
      hooks: "hooks",
      hooksDetail: "Active middlewares on the LangChain agent chain.",
    },
    igino: {
      label: "iGIN0",
      title: "iGIN0 Privacy Search",
      tooltip: (searxng: string, tor: string, cache: string) =>
        `SearXNG: ${searxng} \u00b7 TOR: ${tor} \u00b7 Cache: ${cache}`,
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
    artifacts: "Artifacts",
    todos: "To-dos",
    tor: "TOR",
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
};
