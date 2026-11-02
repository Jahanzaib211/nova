"""Nova's browser layer.

One coherent layer over the three engines Nova browses with:

    agent tool call
         │
         ├─ browser_* tools ──► sandbox Chromium (Playwright over CDP)
         │                        prepare(): stealth · blocking · profile cookies/localStorage
         │                                   downloads → outputs/downloads · recorder · captcha probe
         │
         ├─ web_fetch ────────► escalation ladder (escalation.py)
         │                        1 Browserless /content (profile cookies, settings)
         │                        2 Browserless + stealth launch     ─┐ only on an explicit
         │                        3 sandbox Chromium + user profile  ─┘ block signal (challenge.py)
         │
         └─ web_fetch_many / web_crawl ─► Crawl4AI /crawl (settings it accepts)
                                  URLs a profile matches go via Browserless with cookies,
                                  because Crawl4AI's REST trust boundary forbids cookies.

Shared state: encrypted per-user profiles (profiles.py), the `browser:` config
section (nova.config.browser_config), per-thread toggles (thread_state.py).
"""
