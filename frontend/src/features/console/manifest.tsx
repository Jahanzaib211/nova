import {
  BotIcon,
  CalendarClockIcon,
  FlaskConicalIcon,
  KeyRoundIcon,
  MonitorSmartphoneIcon,
  RadioTowerIcon,
  RefreshCwIcon,
  ServerCogIcon,
} from "lucide-react";

import type { FeatureManifest } from "../types";

import { AgentsPage } from "./pages/agents-page";
import { AutomationPage } from "./pages/automation-page";
import { DevicesPage } from "./pages/devices-page";
import { GatewayPage } from "./pages/gateway-page";
import { LabsPage } from "./pages/labs-page";
import { SecretsPage } from "./pages/secrets-page";
import { UpdatesPage } from "./pages/updates-page";
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
      id: "gateway",
      order: 81,
      group: "connections",
      icon: RadioTowerIcon,
      label: (t) => t.settings.sections.gateway,
      Page: GatewayPage,
      flag: "capabilities",
    },
    {
      id: "devices",
      order: 95,
      group: "connections",
      icon: MonitorSmartphoneIcon,
      label: (t) => t.settings.sections.devices,
      Page: DevicesPage,
      flag: "capabilities",
    },
    {
      id: "workers",
      order: 96,
      group: "connections",
      icon: ServerCogIcon,
      label: (t) => t.settings.sections.workers,
      Page: WorkersPage,
      flag: "jobs",
    },
    {
      id: "agents",
      order: 5,
      group: "agents",
      icon: BotIcon,
      label: (t) => t.settings.sections.agents,
      Page: AgentsPage,
      flag: "capabilities",
    },
    {
      id: "labs",
      order: 15,
      group: "agents",
      icon: FlaskConicalIcon,
      label: (t) => t.settings.sections.labs,
      Page: LabsPage,
      flag: "capabilities",
    },
    {
      id: "automation",
      order: 120,
      group: "agents",
      icon: CalendarClockIcon,
      label: (t) => t.settings.sections.automation,
      Page: AutomationPage,
      flag: "jobs",
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
    {
      id: "updates",
      order: 100,
      group: "system",
      icon: RefreshCwIcon,
      label: (t) => t.settings.sections.updates,
      Page: UpdatesPage,
      flag: "capabilities",
    },
  ],
};
