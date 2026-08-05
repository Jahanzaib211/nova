# Loading a script into TradingView's Pine Editor

**Read this first:** this is inherently fragile. It depends on TradingView's
current DOM and on the user already being logged in, and it will break when
they ship a UI change. It lives in a skill doc precisely so it can be fixed
without a code change. If it fails, fall back to telling the user to paste —
that is a 20-second manual step, not a failure worth grinding on.

Requires the AIO container sandbox. Every `browser_*` tool returns
`Error: this tool requires the container (AIO) sandbox.` on the local sandbox.

## Why `browser_input` will not work here

The Pine Editor is a Monaco-style code editor: a virtualised `<div>` tree with
no `<textarea>` holding the full text. `browser_input` targets real form
controls, so it either fails to find anything or types into a hidden proxy
element that Monaco ignores. Set the editor's model value directly with
`browser_eval` instead.

## Sequence

**1. Open the chart.**

```
browser_navigate  url="https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD"
```

**2. Confirm what actually loaded** — a login wall or a consent dialog is the
usual reason the later steps fail.

```
browser_eval  script="JSON.stringify({url: location.href, title: document.title, loggedIn: !document.querySelector('[data-name=\"header-user-menu-sign-in\"]')})"
```

**3. Open the Pine Editor.** The button label and selector move around; try
in order and stop at the first that works:

```
browser_click  selector="button[data-name='scripteditor']"
browser_click  selector="[data-name='pine-editor-tab']"
browser_click  selector="text=Pine Editor"
```

`browser_click` searches the main frame and then every child frame, so an
editor mounted in an iframe is handled. Timeout is 30s by default
(`DEERFLOW_BROWSER_OP_TIMEOUT_MS`).

**4. Write the script into the Monaco model.**

```
browser_eval  script="""
const src = document.querySelector('.monaco-editor');
if (!src) return 'no monaco editor found';
const model = window.monaco?.editor?.getModels?.()[0];
if (!model) return 'monaco global not exposed';
model.setValue(PINE_SOURCE_HERE);
return 'set ' + model.getValueLength() + ' chars';
"""
```

`browser_eval` accepts multi-statement bodies (it wraps them in an IIFE) and
returns JSON, so objects come back parseable.

Embedding the source: JSON-encode it so quotes and newlines survive —
build the script as `model.setValue(<json string literal>)`.

If `window.monaco` is not exposed, fall back to the clipboard route:

```
browser_eval   script="navigator.clipboard.writeText(PINE_SOURCE); return 'copied';"
browser_click  selector=".monaco-editor"
browser_eval   script="document.execCommand('paste'); return 'pasted';"
```

Clipboard writes need a focused, permissioned page; if that is refused, stop
and hand off to manual paste.

**5. Save and add to chart.**

```
browser_click  selector="[data-name='save']"
browser_click  selector="[data-name='add-script-to-chart']"
```

**6. Verify visually — always.** Do not report success from a click returning
without error; a click can land on a disabled button.

```
screenshot  description="confirm the script compiled and plotted"
```

`screenshot` captures the genuinely visible tab. Check for TradingView's red
error banner at the bottom of the editor before claiming it worked.

## Reading compile errors back

```
browser_eval  script="""
const out = [...document.querySelectorAll('[class*=\"errorMessage\"], [class*=\"console\"] [class*=\"error\"]')]
  .map(e => e.textContent.trim()).filter(Boolean);
return out.length ? out : 'no errors visible';
"""
```

Feed anything found back into `validate_pine.py` — if the validator missed it,
that identifier belongs in `KNOWN_INVALID`.

## When to stop

Two failed selector attempts on the same step means the DOM has moved. Say so
plainly, give the user the `.pine` file, and tell them to paste it. Do not
burn turns guessing selectors.
