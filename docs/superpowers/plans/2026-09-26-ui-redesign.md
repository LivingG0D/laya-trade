# Web UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the Laya Trade dashboard as an "amber instrument panel", merge the Simple/Pro sidebars into one, and fix the header/strip overflow, without changing backend or data flow.

**Architecture:** Keep every element id the JS writes to. Restructure `web/index.html` (header, chart strip, sidebar, mobile tab bar), strip the Simple/Pro mode code from `web/app.js`, `web/js/toobit.js`, `web/js/autotrade.js`, and replace `web/style.css` with a new stylesheet written from scratch on the spec's tokens.

**Tech Stack:** Static HTML + ES modules, lightweight-charts 4.1.3 (vendored), local lucide icon shim (`web/lib/icons.js`), FastAPI static serving, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ui-redesign-design.md`

## Global Constraints

- Colours: `--bg #16181B`, `--panel #1F2226`, `--raised #272B30`, `--line #2C3036`, `--text #E6E3DC`, `--muted #8B8F96`, `--amber #FFB020` (Laya verdict, Analyze, focus ring only), `--long #3FB8A0`, `--short #E0604A`.
- Type: Barlow Condensed 500/600 (headings, verdict, labels), Barlow 400/500 (body), `tabular-nums` for numbers, no monospace. Scale 12/14/16/20/28/44 px. Sentence-case labels, no all-caps eyebrows.
- Radius: 4px controls, 8px panels, 12px dialogs. No glows, gradients, coloured shadows.
- Only unprompted motion: the confidence needle sweep, disabled under `prefers-reduced-motion`.
- Breakpoints: desktop ≥ 1100px, tablet 768–1099px, phone < 768px. No horizontal page scroll at 390px. Tap targets ≥ 44px on phone.
- Icons: only names present in `web/lib/icons.js` (add node arrays there if a new one is needed).
- Copy: sentence case, plain verbs, names from spec §3.8.

---

### Task 1: UI id contract test

**Files:**
- Create: `tests/test_ui_ids.py`

**Interfaces:**
- Produces: a pytest guard later tasks run after every markup change.

- [ ] **Step 1: Write the test**

```python
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def _js_sources():
    return [WEB / "app.js", *sorted((WEB / "js").glob("*.js"))]


def test_every_dom_hook_used_by_js_exists_in_index_html():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'\bid="([^"]+)"', html))
    missing = []
    for src in _js_sources():
        js = src.read_text(encoding="utf-8")
        for dom_id in re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js):
            if dom_id not in html_ids:
                missing.append(f"{src.name}: #{dom_id}")
        for kind, key in re.findall(r'\[data-(at|pos)="([^"$]+)"\]', js):
            if f'data-{kind}="{key}"' not in html:
                missing.append(f"{src.name}: [data-{kind}={key}]")
        for key in re.findall(r'atAll\("([^"]+)"\)', js):
            if f'data-at="{key}"' not in html:
                missing.append(f"{src.name}: [data-at={key}]")
        for key in re.findall(r'field\("([^"]+)"\)', js):
            if f'data-pos="{key}"' not in html:
                missing.append(f"{src.name}: [data-pos={key}]")
    assert not missing, "JS hooks with no element in index.html:\n" + "\n".join(missing)
```

- [ ] **Step 2: Run it** — `pytest tests/test_ui_ids.py -v`. Expected on the current tree: PASS (characterisation). It turns red in Task 2 as soon as JS references a new id before the markup has it.
- [ ] **Step 3: Commit** — `git add tests/test_ui_ids.py && git commit -m "test: guard JS DOM hooks against index.html"`

### Task 2: Markup and JS restructure (mode merge)

**Files:**
- Modify: `web/index.html` (head fonts, header, chart strip, sidebar, mobile tabs, copy)
- Modify: `web/app.js` (remove mode code, calculator, simple UI; add needle/risk badge/details persistence; chart colours; analyze restore fix; mobile Position tab)
- Modify: `web/js/toobit.js` (drop simple card; add `proSugHeadline`; confirm before close)
- Modify: `web/js/autotrade.js` (drop simple card + log snippet; add `proAtStatusMsg`)
- Modify: `web/js/feedback.js` (none expected)
- Test: `tests/test_ui_ids.py`

**Interfaces:**
- Produces ids: `confidenceNeedle`, `signalRiskBadge`, `proSugHeadline`, `proAtStatusMsg`; mobile tab `data-tab="position"`; `<details data-persist="...">` sections.
- Removes ids: all `simple*`, `calc*`, `rr*`, `btnSimpleMode`, `btnProMode`, `simpleModeContainer`, `proModeContainer`, `proAtLogSnippet`, telemetry duplicates (`telemRsi*`, `telemTrend*`, `telemSupport`, `telemResistance`, `telemObi*`, `telemHtf*`), unused `proPosSym`, `proPosSideBadge`.

- [ ] **Step 1:** Edit JS first to reference the new ids and drop the old ones (toobit.js, autotrade.js, app.js). Run `pytest tests/test_ui_ids.py` — expect FAIL listing `confidenceNeedle`, `signalRiskBadge`, `proSugHeadline`, `proAtStatusMsg`.
- [ ] **Step 2:** Rewrite `web/index.html` body per spec §2/§3 (header groups, strip + toggle row, merged sidebar, bottom tab bar with Position tab, drawer). Keep dialogs' ids; update their copy.
- [ ] **Step 3:** Run `pytest tests/test_ui_ids.py -v` — expect PASS. Run `pytest -q` — expect all green.
- [ ] **Step 4:** `npx --yes eslint@8 --no-eslintrc --env browser,es2022 --parser-options=sourceType:module --global LightweightCharts,lucide --rule 'no-undef:error' --rule 'no-unused-vars:warn' web/app.js web/js/*.js` — expect no errors.
- [ ] **Step 5: Commit** — `feat(ui): merge simple/pro sidebar and restructure header`

### Task 3: New stylesheet

**Files:**
- Replace: `web/style.css`

- [ ] **Step 1:** Collect every class the markup and JS emit: `grep -ohE 'class(Name)?="[^"]+"|className = [^;]+|classList\.(add|remove|toggle)\("[^"]+"' web/index.html web/app.js web/js/*.js`. Every class there that carries visual state (`hide`, `active`, `pnl-green`, `pnl-red`, `price-up`, `price-down`, `flash-up`, `flash-down`, `long`, `short`, `neutral`, `danger`, `warn`, `safe`, `status-*`, `badge-*`, `alignment-pill *`, `at-*`, `log-*`, `coin-*`, `toast-*`, `collapsed`, `focused`, `is-on`, `is-off`, `is-stale`, `sig-*`, `side-*`, `fullscreen-active`, `active-squeeze`, `inst-pill-dot *`, `tag-long`, `tag-short`, `sl-val`, `tp-val`) must have a rule.
- [ ] **Step 2:** Write `web/style.css`: tokens → base/reset → dialog reset (keep `.modal-overlay:not([open]) { display:none !important }` and `::backdrop`) → layout grid → header → chart chrome → sidebar sections → position card → auto-trader block → details → drawer/table → dialogs (coin search, toobit, auto-trade desk/settings/safety/logs) → toasts → tablet → phone → reduced motion.
- [ ] **Step 3:** Run `pytest -q` (static caching test still serves `/static/style.css`).
- [ ] **Step 4: Commit** — `feat(ui): amber instrument panel stylesheet`

### Task 4: Browser verification and fixes

- [ ] **Step 1:** Start server `python main.py --port 8123 --host 127.0.0.1` in background.
- [ ] **Step 2:** At 1440×900, 1024×768, 390×844: screenshot; check `document.documentElement.scrollWidth <= innerWidth`, no console errors, header children not overlapping (compare `getBoundingClientRect` of adjacent groups).
- [ ] **Step 3:** Exercise: Analyze (verdict + needle update), open/close all three dialogs (Esc and close button), Simulate long → position card fills → Close position (confirm) → empty state, auto-trader block renders (do not arm), details open state persists across reload, mobile tabs switch views.
- [ ] **Step 4:** Fix defects found; re-run Step 2–3; `pytest -q`.
- [ ] **Step 5:** Stop server. Commit fixes — `fix(ui): responsive and interaction fixes from browser pass`.
