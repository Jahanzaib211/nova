import {
  BotIcon,
  FlaskConicalIcon,
  KeyRoundIcon,
  MonitorSmartphoneIcon,
  ServerCogIcon,
} from "lucide-react";

import type { FeatureManifest } from "../types";

import { AgentsPage } from "./pages/agents-page";
import { DevicesPage } from "./pages/devices-page";
import { LabsPage } from "./pages/labs-page";
import { SecretsPage } from "./pages/secrets-page";
import { WorkersPage } from "./pages/workers-page";

/**
 * Console parity: the settings pages that make Nova's operator surface as
 * complete as the consoles users compare it to. Every page here is a view
 * over capability operations (src/core/capabilities) — nothing is bespoke,
 * so the harness and the MCP server see exactly what the page sees.
 */
export const consoleManifest: FeatureManifest = {
  id: "console",
  settings: [
    {
      id: "devices",
      // Browser sessions and harness tokens: credentials, so it sits with
      // Secrets rather than under Connections.
      order: 20,
      group: "privacy",
      icon: MonitorSmartphoneIcon,
      label: (t) => t.settings.sections.devices,
      Page: DevicesPage,
      flag: "capabilities",
    },
    {
      id: "workers",
      order: 86,
      group: "system",
      icon: ServerCogIcon,
      label: (t) => t.settings.sections.workers,
      Page: WorkersPage,
      flag: "jobs",
      // Lists hostnames, pids and every user's running job ids.
      adminOnly: true,
    },
    {
      id: "runtimes",
      order: 5,
      group: "agents",
      icon: BotIcon,
      label: (t) => t.settings.sections.runtimes,
      Page: AgentsPage,
      flag: "capabilities",
    },
    {
      id: "labs",
      // Read-only server feature flags: system info, not an agent tool.
      order: 95,
      group: "system",
      icon: FlaskConicalIcon,
      label: (t) => t.settings.sections.labs,
      Page: LabsPage,
      flag: "capabilities",
    },
    {
      id: "secrets",
      order: 10,
      group: "privacy",
      icon: KeyRoundIcon,
      label: (t) => t.settings.sections.secrets,
      Page: SecretsPage,
      flag: "capabilities",
      adminOnly: true,
    },
  ],
};
