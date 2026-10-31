import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { SandboxLiveProvider, useSandboxThreadId } from "@/core/sandbox/live";

function Probe({ id }: { id: string | null }) {
  return createElement("span", null, String(useSandboxThreadId(id)));
}

const render = (live: boolean | undefined, id: string | null) =>
  renderToStaticMarkup(
    live === undefined
      ? createElement(Probe, { id })
      : createElement(
          SandboxLiveProvider,
          { value: live },
          createElement(Probe, { id }),
        ),
  );

test("passes the thread id through by default", () => {
  // Every panel outside a chat (and every existing test) keeps polling as before.
  expect(render(undefined, "t-1")).toBe("<span>t-1</span>");
});

test("passes the thread id through once the thread exists", () => {
  expect(render(true, "t-1")).toBe("<span>t-1</span>");
});

test("withholds the id of a thread that exists only in the browser", () => {
  // A new chat's client-generated id: polling it 404s once a second.
  expect(render(false, "t-1")).toBe("<span>null</span>");
});
