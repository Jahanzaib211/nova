/**
 * Geometry regression for the chat page's todo panel (2026-10-31).
 *
 * The panel is a sibling of the composer and its own box ends exactly where
 * the composer begins. It used to carry a 16px downward translate, which
 * painted it 16px INTO the composer: the composer's rounded top border and
 * its first input row showed through the near-transparent panel, the last
 * todo row was clipped by the composer, and a 16px gap was left above the
 * panel's top border.
 *
 * Nothing fails in a unit test when a panel is painted over the composer --
 * both mistakes are plain class strings -- so the classes are pinned here.
 */

import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { TodoList } from "@/components/workspace/todo-list";
import { I18nProvider } from "@/core/i18n/context";

/** The class list of the panel root (the outermost element TodoList renders). */
function panelRootClass(collapsed: boolean): string {
  const html = renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">
      <TodoList
        todos={[{ content: "First step", status: "in_progress" }]}
        collapsed={collapsed}
      />
    </I18nProvider>,
  );
  const match = /^<div class="([^"]*)"/.exec(html);
  expect(match).not.toBeNull();
  return match?.[1] ?? "";
}

const CHAT_PAGES = [
  "src/app/workspace/chats/[thread_id]/page.tsx",
  "src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
];

/** The `<TodoList ... />` usage of a page, as source text. */
function todoListUsage(page: string): string {
  const src = readFileSync(page, "utf-8");
  const start = src.indexOf("<TodoList");
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf("/>", start);
  return src.slice(start, end);
}

describe("TodoList panel geometry", () => {
  test("the panel is opaque -- no translucent background", () => {
    for (const collapsed of [true, false]) {
      const rootClass = panelRootClass(collapsed);
      expect(rootClass).toContain("bg-background");
      // bg-background/5 and friends: the pane behind the panel showed through.
      expect(rootClass).not.toMatch(/bg-background\/\d/);
    }
  });

  test("the panel is not translated over the composer", () => {
    for (const collapsed of [true, false]) {
      const rootClass = panelRootClass(collapsed);
      // translate-y-4 painted the panel 16px into the composer.
      expect(rootClass).not.toMatch(/translate-y-/);
      expect(rootClass).not.toContain("translate");
    }
  });

  test("the panel keeps a top border only -- no double border at the seam", () => {
    const rootClass = panelRootClass(true);
    expect(rootClass).toContain("border-b-0");
    expect(rootClass).toContain("rounded-t-xl");
  });
});

describe("chat page todo panel wiring", () => {
  test.each(CHAT_PAGES)("%s renders the panel opaque", (page) => {
    const usage = todoListUsage(page);
    expect(usage).toContain('className="bg-background"');
    expect(usage).not.toMatch(/className="bg-background\/\d/);
  });

  test.each(CHAT_PAGES)(
    "%s lifts the welcome-mode panel by exactly the composer's own translate",
    (page) => {
      const src = readFileSync(page, "utf-8");
      // In welcome mode the composer moves itself up 8px (16px from sm). A
      // panel that did not move with it was overlapped by exactly that much:
      // the composer slid over the panel's last row.
      expect(src).toContain('isWelcomeMode && "-translate-y-2 sm:-translate-y-4"');
      expect(src).toContain('"absolute -top-2 sm:-top-4"');
    },
  );

  test.each(CHAT_PAGES)(
    "%s shows no to-dos on a brand-new thread",
    (page) => {
      const src = readFileSync(page, "utf-8");
      // thread.values can still hold the previous thread's state for a
      // render, which painted its to-dos on the empty start screen.
      expect(src).toMatch(/const hasTodos = !isNewThread && /);
    },
  );
});

describe("collapsed and expanded states", () => {
  const TODOS = [
    { content: "Probe sandbox", status: "completed" as const },
    { content: "Restart dev server", status: "in_progress" as const },
    { content: "Run browser_check", status: "pending" as const },
  ];

  function render(collapsed: boolean): string {
    return renderToStaticMarkup(
      <I18nProvider initialLocale="en-US">
        <TodoList todos={TODOS} collapsed={collapsed} />
      </I18nProvider>,
    );
  }

  test("collapsed is the header alone -- no strip for a sliced row to show through", () => {
    const html = render(true);
    // overflow-hidden clips at the padding box, so any collapsed list strip
    // showed the top half of its first row.
    expect(html).not.toContain("<ol");
    // The step in progress is surfaced in the header instead.
    expect(html).toContain("Restart dev server");
    expect(html).toContain("1/3");
  });

  test("expanded shows every row, sized to whole rows", () => {
    const html = render(false);
    expect(html).toContain("<ol");
    expect(html.match(/<li /g)).toHaveLength(3);
    // 4 rows of 32px + 4px padding top and bottom: never half a row.
    expect(html).toContain("max-height:136px");
  });

  test("the tab is inset by the composer's corner radius and shares its border", () => {
    const rootClass = /^<div class="([^"]*)"/.exec(render(true))?.[1] ?? "";
    // rounded-2xl composer = 16px corners, so mx-4 lands the tab's edges on
    // its straight top border.
    expect(rootClass).toContain("mx-4");
    expect(rootClass).toContain("border-input/50");
  });
});
