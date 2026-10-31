import type { ComponentType, JSX } from "react";
import type { Components } from "streamdown";

/**
 * The component overrides we hand to Streamdown.
 *
 * Streamdown types its `components` map as an intersection: a mapped type
 * over the JSX intrinsics *and* an open index signature every custom
 * renderer has to satisfy. The index signature demands a component whose
 * props are `Record<string, unknown> & ExtraProps`, so a renderer typed
 * against the element it replaces (`ComponentProps<"a">`) is rejected — the
 * index signature widens every known prop to `unknown`.
 *
 * The runtime contract did not change: streamdown still passes the element's
 * own props plus `node`. So we keep the precise per-element types at the
 * call site and adapt once, here.
 */
export type StreamdownComponentOverrides = {
  [Key in keyof JSX.IntrinsicElements]?: ComponentType<
    JSX.IntrinsicElements[Key]
  >;
};

/**
 * Adapt our typed renderer map to Streamdown's `components` prop.
 *
 * The cast is the single adaptation point for the upstream index signature;
 * every call site keeps its own precise component types.
 */
export function streamdownComponents(
  components: StreamdownComponentOverrides,
): Components {
  return components as Components;
}
