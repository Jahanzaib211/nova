import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SidePanel } from "@/components/workspace/panels/side-panel";

/**
 * <SidePanel> is the one column chrome for resizable side surfaces (Agent's
 * Computer today). Pins the contract the chat layout relies on: a closed
 * panel keeps its children mounted but takes no width and no focus; the
 * handle is an accessible separator that reports its width.
 */
function render(open: boolean) {
  const ref = createRef<HTMLDivElement>();
  return renderToStaticMarkup(
    <SidePanel
      open={open}
      storageKey="test-panel"
      containerRef={ref}
      resizeLabel="Resize panel"
    >
      <div data-testid="child">content</div>
    </SidePanel>,
  );
}

describe("SidePanel", () => {
  it("collapses to zero width but keeps its children mounted when closed", () => {
    const html = render(false);
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("invisible");
    expect(html).toMatch(/style="width:0(px)?"/);
    expect(html).toContain('data-testid="child"');
  });

  it("opens at the default width with an accessible separator", () => {
    const html = render(true);
    expect(html).toContain('data-open="true"');
    expect(html).toContain('role="separator"');
    expect(html).toContain('aria-orientation="vertical"');
    expect(html).toContain('aria-valuenow="640"');
    expect(html).toContain('aria-label="Resize panel"');
    expect(html).toContain("touch-none");
  });
});
