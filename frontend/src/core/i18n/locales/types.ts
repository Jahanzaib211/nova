import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
    loadMore: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    agentBusy: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
  };

  home: {
    docs: string;
    blog: string;
    menu: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    agents: string;
    channels: string;
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    apiDisabledTitle: string;
    apiDisabledDescription: string;
    loadErrorTitle: string;
    loadErrorDescription: string;
    retry: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepCheckErrorWithDetail: string;
    nameStepApiDisabledError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    skillsDialogDescription: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    logout: string;
    gatewayUnavailable: string;
    gatewayUnavailableRetrying: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
  };

  // Chats
  chats: {
    searchChats: string;
    loadMoreToSearch: string;
    loadingMore: string;
    loadOlderChats: string;
  };

  // Channels
  channels: {
    title: string;
    connect: string;
    modify: string;
    reconnect: string;
    disconnect: string;
    connected: string;
    notConnected: string;
    pending: string;
    revoked: string;
    disabled: string;
    unconfigured: string;
    unavailable: string;
    unavailableShort: string;
    setupTitle: (name: string) => string;
    setupEditTitle: (name: string) => string;
    setupDescription: string;
    saveAndConnect: string;
    saveChanges: string;
    descriptions: Record<string, string>;
    connectedAs: (name: string) => string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      channels: string;
      models: string;
      memory: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    models: {
      title: string;
      description: string;
      loadError: string;
      sourceConfig: string;
      sourceRuntime: string;
      addButton: string;
      addLlamaCppButton: string;
      addOllamaButton: string;
      addFireworksButton: string;
      addAmdCloudButton: string;
      addTitle: string;
      editTitle: string;
      formDescription: string;
      fieldName: string;
      fieldDisplayName: string;
      fieldProvider: string;
      fieldCustomClass: string;
      fieldModelId: string;
      fieldBaseUrl: string;
      fieldApiKey: string;
      fieldThinking: string;
      fieldReasoningEffort: string;
      fieldVision: string;
      apiKeyUnchanged: string;
      providerOpenAICompatible: string;
      providerAnthropic: string;
      providerCustom: string;
      saveButton: string;
      testButton: string;
      confirmDelete: string;
      created: string;
      updated: string;
      deleted: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      adminRequired: string;
      empty: string;
    };
    channels: {
      title: string;
      description: string;
      disabled: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      creditsTitle: string;
      creditsDescription: string;
      creditsLeftToday: string;
      creditsUnlimited: string;
      creditsResets: string;
      creditsUsedToday: string;
      creditsRequestMore: string;
      creditsRequestReason: string;
      creditsRequestSend: string;
      creditsRequestPending: string;
      creditsRequestApproved: string;
      creditsRequestSent: string;
      referralTitle: string;
      referralDescription: string;
      referralYourLink: string;
      referralCopy: string;
      referralCopied: string;
      referralCount: string;
      referralBonusActive: string;
      byokTitle: string;
      byokDescription: string;
      byokProvider: string;
      byokApiKey: string;
      byokSave: string;
      byokRemove: string;
      byokActive: string;
      byokSaved: string;
      billingTitle: string;
      billingDescription: string;
      billingCurrentPlan: string;
      billingUpgrade: string;
      billingManage: string;
      changeEmailTitle: string;
      changeEmailDescription: string;
      newEmail: string;
      updateEmail: string;
      emailChangedSuccess: string;
      invalidEmail: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };

  agentComputer: {
    header: string;
    thinking: string;
    usingTerminal: string;
    usingBrowser: string;
    usingEditor: string;
    taskProgress: string;
    noLogs: string;
    close: string;
    live: string;
    moreActions: string;
    pushToGithub: string;
    downloadAllZip: string;
    downloadActiveFile: string;
    verifyResult: {
      passed: (count: number) => string;
      failed: (count: number) => string;
      consoleErrors: (count: number) => string;
    };
    tabs: {
      files: string;
      terminal: string;
      editor: string;
      browser: string;
      activity: string;
      review: string;
      privacy: string;
    };
    files: {
      empty: string;
      repository: string;
      running: (count: number) => string;
    };
    workspace: {
      title: string;
      index: string;
      indexing: string;
      reindex: string;
      monorepo: string;
      projects: string;
      symbols: string;
      commands: string;
      impactFooter: (count: number) => string;
      indexedBanner: (symbols: number, projects: number, commands: number, language: string) => string;
      kernelTitle: string;
      kernelScans: string;
      kernelAvgScan: string;
      kernelCacheHits: string;
      liveScanned: (symbols: number, ms: number) => string;
      livePlan: (steps: number, risk: string) => string;
      liveCacheHit: string;
      liveCacheMiss: string;
    };
    status: {
      writing: (filename: string, lines?: string) => string;
      usingEditor: string;
      editing: (filename: string) => string;
      reading: (filename: string) => string;
      usingTerminal: string;
      searchingFiles: string;
      searchingContent: string;
      delegatingToSubagent: string;
      scaffoldingProject: string;
      usingBrowser: string;
      isThinking: string;
      isIdle: string;
    };
    terminal: {
      tab: string;
      stream: string;
      shell: string;
      interactiveTitle: string;
      noOutput: string;
      noOutputHint: string;
      running: string;
    };
    editor: {
      startWriting: string;
      diff: string;
      file: string;
      lines: (count: number) => string;
      writing: string;
    };
    browser: {
      back: string;
      forward: string;
      reload: string;
      live: string;
      compiling: string;
      switchPreview: string;
      selfTest: string;
      watchAgentBrowser: string;
      vnc: string;
      desktop: string;
      mobile: string;
      openNewTab: string;
      testingInBrowser: string;
      selfTestPassed: string;
      selfTestIssues: string;
      testedPort: (port: string) => string;
      livePreview: string;
      devServerCompiling: string;
      previewWillAppear: string;
      previewWillAppearLine2: string;
      watchLiveBrowser: string;
      projectType: {
        react: string;
        python: string;
        markdown: string;
        code: string;
      };
      projectLabel: (type: string) => string;
      switchToEditor: string;
      switchToEditorPrefix: string;
      switchToEditorSuffix: string;
      startLivePreview: string;
      fileMissing: (name: string) => string;
    };
    activity: {
      title: (count: number) => string;
      exportAuditLog: string;
      empty: string;
    };
    review: {
      generating: string;
      needsLook: string;
      mostlyFine: string;
      looksClean: string;
      codeReview: string;
      regenerate: string;
      download: string;
      noRiskyActions: string;
      riskFlags: string;
      changedFiles: string;
      detectedChecks: string;
      noChanges: string;
      kernelVerdictTitle: string;
      kernelVerdictValid: string;
      kernelVerdictInvalid: string;
      kernelVerdictSteps: (count: number) => string;
    };
    privacy: {
      title: string;
      sourceHealth: string;
      searxng: string;
      tor: string;
      healthy: string;
      unhealthy: string;
      available: string;
      unavailable: string;
      cache: string;
      size: string;
      hitRate: string;
      ttl: string;
      audit: string;
      total: string;
      errors: string;
      torUsage: string;
      toggleLabel: string;
    };
    skillLauncher: {
      runSkill: string;
    };
  };

  runtimeBar: {
    status: {
      healthy: string;
      healthyTitle: string;
      healthyBody: string;
      degraded: (count: number) => string;
      degradedTitle: (count: number) => string;
      degradedBody: string;
      critical: (count: number) => string;
      criticalTitle: (count: number) => string;
      criticalBody: string;
    };
    skills: {
      none: string;
      overflow: (count: number) => string;
      overflowHint: string;
    };
    metrics: {
      tools: string;
      toolsDetail: string;
      subagents: string;
      subagentsDetail: string;
      subagentsConcurrency: (n: number) => string;
      hooks: string;
      hooksDetail: string;
    };
    igino: {
      label: string;
      title: string;
      tooltip: (searxng: string, tor: string, cache: string) => string;
    };
    circuits: {
      open: (count: number) => string;
      autoRecovers: string;
    };
    offline: string;
    offlineHint: string;
  };

  // ai-elements (message rendering primitives)
  aiElements: {
    context: {
      title: string;
      ariaLabel: string;
      totalCost: string;
      input: string;
      output: string;
      reasoning: string;
      cache: string;
    };
    reasoning: {
      thinking: string;
      thoughtFew: string;
      thought: (seconds: number) => string;
    };
    webPreview: {
      enterUrl: string;
      previewTitle: string;
    };
  };

  // Landing page sections
  landing: {
    footer: {
      license: string;
    };
    hero: {
      getStarted: string;
    };
    caseStudy: {
      title: string;
      subtitle: string;
    };
    community: {
      subtitle: string;
    };
    sandbox: {
      title: string;
    };
    skills: {
      title: string;
    };
    whatsNew: {
      title: string;
      subtitle: string;
    };
    skillsAnimation: {
      agentLabel: string;
      loadingSkill: (skillName: string) => string;
      generating: (file: string) => string;
      executing: (script: string) => string;
    };
  };

  // Accessibility labels for icon-only buttons / decorative elements
  a11y: {
    runtimeCapabilities: string;
    artifactPreview: string;
    noArtifact: string;
    dragResize: string;
    editSkills: string;
    skillsFor: (agentName: string) => string;
    chat: string;
    panels: string;
    artifacts: string;
    todos: string;
    tor: string;
    open: string;
    thinking: string;
  };

  // Setup & login pages
  auth: {
    setup: {
      loading: string;
      createAdmin: string;
      passwordMin: string;
      confirmPassword: string;
      yourEmail: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
    };
    login: {
      passwordPlaceholder: string;
      submitLabel: string;
      setupPrompt: string;
    };
  };
}
