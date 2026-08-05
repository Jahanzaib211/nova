import type { ToolCall } from "@langchain/core/messages";
import type { AIMessage } from "@langchain/langgraph-sdk";

import type { Translations } from "../i18n";
import { hasToolCalls } from "../messages/utils";

export function explainLastToolCall(message: AIMessage, t: Translations) {
  if (hasToolCalls(message)) {
    const lastToolCall = message.tool_calls![message.tool_calls!.length - 1]!;
    return explainToolCall(lastToolCall, t);
  }
  return t.common.thinking;
}

export function explainToolCall(toolCall: ToolCall, t: Translations) {
  if (toolCall.name === "web_search" || toolCall.name === "image_search") {
    return t.toolCalls.searchFor(toolCall.args.query);
  } else if (toolCall.name === "web_fetch") {
    return t.toolCalls.viewWebPage;
  } else if (toolCall.name === "present_files") {
    return t.toolCalls.presentFiles;
  } else if (toolCall.name === "write_todos") {
    return t.toolCalls.writeTodos;
  } else if (toolCall.args.description) {
    // Agent-authored and task-specific, so it beats any static label.
    return toolCall.args.description;
  } else if (toolCall.name === "get_ohlcv") {
    return t.toolCalls.fetchMarketData;
  } else if (toolCall.name === "compute_indicators") {
    return t.toolCalls.computeIndicators;
  } else if (toolCall.name === "backtest_signals") {
    return t.toolCalls.backtestSignals;
  } else {
    return t.toolCalls.useTool(toolCall.name);
  }
}
