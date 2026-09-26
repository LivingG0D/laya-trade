# Web UI redesign — design spec

Date: 2026-09-26
Scope: `web/index.html`, `web/style.css`, `web/app.js`, `web/js/*.js`. No backend or API changes.

## Goal

Give the Laya Trade dashboard its own visual identity and fix the UX problems in the current UI, without changing features or data flow:

- Header controls collide at 1440px (price stats, Simple/Pro switch, status chips; a `$4.93` value spills out).
- The indicator strip above the chart is clipped.
- The sidebar stacks many equal-weight bordered cards; nothing leads.
- Simple and Pro modes duplicate the verdict, plan, auto-trader and position cards, and the mode silently overrides the EMA / Target Lines checkboxes.

Decisions already made with the user:

- Full redesign, same features and backend.
- Balanced weight across chart, AI verdict, auto-trader and positions.
- Visual direction: "amber instrument panel".
- Merge Simple and Pro into one sidebar. Drop the return calculator and the beginner cheat sheet.
- Phone must support full trading, chart included.
- Approach A: keep every element id the JS writes to, rewrite `style.css` from scratch, restructure only the header and sidebar markup.

## 1. Visual system

### Colour tokens

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#16181B` | page background (graphite) |
| `--panel` | `#1F2226` | header, sidebar, dialogs |
| `--raised` | `#272B30` | inputs, hovered rows |
| `--line` | `#2C3036` | 1px dividers and borders |
| `--text` | `#E6E3DC` | primary text |
| `--muted` | `#8B8F96` | labels, secondary text |
| `--amber` | `#FFB020` | Laya verdict, Analyze button, focus ring — nothing else |
| `--long` | `#3FB8A0` | long direction, positive PnL |
| `--short` | `#E0604A` | short direction, negative PnL, kill switch |

No neon glows, gradients, or coloured shadows. The chart (`createChart` in `app.js`) uses the same palette: `--bg` background, `--long`/`--short` candles, `--line` grid, `--muted` axis text.

### Type

- Barlow Condensed 500/600: headings, the verdict word, small labels.
- Barlow 400/500: body text.
- Prices, sizes and PnL use `font-variant-numeric: tabular-nums`. No monospace face.
- Scale: 12 / 14 / 16 / 20 / 28 / 44 px.
- Labels are sentence case in `--muted`. No all-caps eyebrow labels.
- Loaded from Google Fonts (replaces the current Inter + JetBrains Mono link).

### Shape and spacing

- Radius by hierarchy: 4px controls, 8px panels, 12px dialogs.
- Sidebar sections are divided by a 1px `--line`, not wrapped in separate shadowed cards.

### Signature element

The Laya verdict: the direction word (Long / Short / Wait) at 44px Barlow Condensed, and confidence as an instrument scale — a 0–100 track with tick marks and an amber needle. The needle sweeps to its new value when a new signal arrives. This is the only non-user-triggered motion on the page and is disabled under `prefers-reduced-motion: reduce`.

## 2. Layout

### Desktop (≥ 1100px)

```
┌ header 52px ──────────────────────────────────────────────────────────────┐
│ Laya  BTCUSDT.P▾  1m 5m 15m 1h 4h │ price  change  H  L  Vol │ ● Feed  ● Toobit  ● Auto-trader  [Analyze] │
├───────────────────────────────────────────────────────┬───────────────────┤
│ indicator strip (wraps, never clips)                  │ Laya verdict       │
│ chart toggles row                                     │ Your position      │
│                                                       │ Auto-trader        │
│ chart (fills)                                         │ ▸ Why Laya chose   │
│                                                       │ ▸ Outcome odds     │
│                                                       │ ▸ Levels & indic.  │
├───────────────────────────────────────────────────────┴───────────────────┤
│ Signal history drawer (collapsible)                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

- Sidebar fixed at 380px and scrolls on its own. Verdict and position stay visible at the top; the sections below scroll.
- Header: the left group (brand, coin, timeframes) and right group (status chips, Analyze) keep their width; the middle price group gives way.
  - Below 1280px: 24h low and volume hide; 24h high moves into the price's `title` tooltip.
  - Below 1280px status chips show only the dot, with an `aria-label`.
  - The Simple/Pro switch is removed. The audio toggle moves into a popover on the Feed chip.
- Indicator strip wraps to a second line instead of clipping. Chart toggles (EMA, BB, Targets, Position, Auto-analyze, Fit, Fullscreen) sit on their own row as labelled switches.
- History drawer stays at the bottom as a collapsible bar.

### Tablet (768–1099px)

Sidebar moves below the chart at full width, in two columns (verdict and position left; auto-trader and details right). Chart height `min(60vh, 560px)`.

### Phone (< 768px)

```
┌ BTCUSDT.P▾   price  change   [Analyze] ┐  header row 1
│ 1m 5m 15m 1h 4h            ● ● ●       │  header row 2
├────────────────────────────────────────┤
│ indicator strip (scrolls sideways)     │
│ chart (fills to tab bar)               │
│ position HUD with Close (when open)    │
├────────────────────────────────────────┤
│ Chart │ Laya ● │ Position │ History    │  bottom tab bar
└────────────────────────────────────────┘
```

- The existing top tabs (`#mobileViewTabs`) move to a bottom tab bar and gain a Position tab showing the position card and auto-trader block.
- The Laya tab carries a dot in the verdict colour (`mobileTabSignalPill`).
- The chart position HUD with Close stays on the chart whenever a position is open.
- Dialogs open as full-screen sheets.
- Tap targets are at least 44px.

### Alignment and interaction

- Everything left-aligned. Numbers in the plan and position tables right-aligned.
- Shortcuts kept: Space = analyze, 1–5 = timeframe. `S` (mode toggle) removed.
- Every interactive element gets a visible amber focus ring (`:focus-visible`).

## 3. Components

### 3.1 Laya verdict (replaces S1, S4, P1)

- Built from the Pro Signal Card, keeping its ids: `signalCard`, `signalBadge`, `signalAction`, `confidenceVal`, `signalSubtext`, `targetEntry`/`targetEntrySub`, `targetSL`/`targetSLRoe`, `targetBE`/`targetBERoe`, `targetTP1`/`targetTP1Roe`, `targetTP2`/`targetTP2Roe`, `targetLiq`/`targetLiqDist`, `targetRRR`, `targetHorizon`, `sizingShieldBanner`, `targetRecMargin`, `targetSizingRule`.
- Adds the confidence needle (new id `confidenceNeedle`, driven from `signal.confidence_pct` in `applySignal`) and a squeeze-risk badge (new id `signalRiskBadge`, same 60/30 thresholds as `squeezeRiskVal`).
- Plan shown as a two-column table: entry, TP1, TP2, break-even, stop-loss, liquidation, each with its ROE. Reward/risk, horizon and suggested margin on one footer line.
- `btnCopyTradePlan` kept; its handler reads from the merged card's fields.

### 3.2 Your position (replaces S3, P3)

- Built from `#proPositionCard` with its `data-pos=*` block, so `fillPositionCard` is unchanged.
- AI advice box keeps `proSugBadge`, `proAlignmentPill`, `proSugSl`, `proSugTp`, `proSugText`, and adds the headline previously shown only in Simple: new id `proSugHeadline`, written by `renderToobitPositions` where it wrote `simpleSugHeadline`.
- Buttons: `btnProFlashClose` (labelled "Close position"), `btnProSimLong`, `btnProSimShort`.
- Empty state text: "No open position. Simulate one to see Laya's advice."

### 3.3 Auto-trader (replaces S2, P2)

- One block: toggle (`data-at="toggle"`), status message, sizing, daily PnL (`data-at="daily-pnl"`), win rate (`data-at="winrate"`), circuit-breaker state, Configure (`data-at="config"`), Kill switch (`data-at="kill"`).
- Keeps `proAtModeBadge`, `proAtStatus`, `proAtSizing`, `proAtCircuit`. Status message text (previously `simpleAtStatusText`) goes to new id `proAtStatusMsg`, written by `renderAutoTradeState`.
- Log snippet (`proAtLogSnippet`) removed; logs remain in the auto-trade dialog.

### 3.4 Details (replaces S6, P4, P5, P6)

Three `<details>` sections, collapsed by default, open state remembered in `localStorage` (per-viewer convenience, wrapped in try/catch):

- Why Laya chose this: `reasonsList` (`signal.reasons`).
- Outcome odds: the five probability rows (`prob*`/`bar*`), `latencyBadge`, `squeezeRiskVal`.
- Levels and indicators: pivot gauge (`gaugeSupport`, `gaugePrice`, `gaugeResistance`, `pivotGaugePin`) plus MACD, volume spike, ADX/regime and squeeze (`telemMacd*`, `telemVol*`, `telemAdx`, `telemRegimeState`, `telemSqueeze*`). The telemetry rows that duplicate the chart strip (RSI, HTF, OBI, trend) and the duplicate support/resistance are removed, together with the code that writes them.

### 3.5 Removed

- `#simpleModeContainer` and everything in it, including the Return Calculator (`calcPresetGroup`, `calcWinVal`, `calcLossVal`) and the Beginner Cheat Sheet.
- JS: `updateSimpleModeUI`, `updateDemoCalculator`, the calculator preset handler, `setMode`, the mode button listeners, the `S` shortcut, `currentMode` and the `laya_mode` localStorage key.
- `renderAutoTradeState` and `renderToobitPositions` stop writing `simple*` ids.
- Chart behaviour after removing the mode: the EMA and Target Lines checkboxes alone control those series. Default on first load: EMA on, targets on.

### 3.6 Header and chart chrome

- Header markup restructured into left / middle / right groups as in §2. Ids kept: `coinSelectorWrap`, `coinSelectorBtn`, `coinBtnIcon`, `coinBtnSymbol`, `symbolSelect`, `livePrice`, `priceChange`, `high24h`, `low24h`, `volume24h`, `btnAudioToggle`, `audioIcon`, `wsStatusText`, `btnToobitConnect`, `toobitNavStatus`, `btnAutoTradeModal`, `navAutoTradeDot`, `navAutoTradePill`, `analyzeBtn`.
- Fix: `triggerAnalyze` restores `analyzeBtn` with the wrong icon and drops the Space hint (`app.js:1469`). Restore the original markup instead.

### 3.7 Dialogs

`coinSearchModal`, `toobitModal`, `autoTradeModal` keep their structure and ids; restyled to the tokens (12px panel, title row with close button, underline-style tabs). Full-screen sheet below 768px. Existing behaviour (native `<dialog>`, `autofocus` on close buttons) unchanged.

### 3.8 Copy

Plain language, sentence case, one name per action:

| Current | New |
|---|---|
| Autonomous Algorithmic Desk / AI Auto-Trader | Auto-trader |
| System-1 Probabilities | Outcome odds |
| Wait & Relax | Wait — "No clear edge. Wait for a setup." |
| Analyze Market | Analyze (toast: "Analysis updated") |
| Flash close | Close position (confirm names symbol and side) |
| Toobit Position & AI Advice / Your Active Trade | Your position |

Errors say what failed and how to fix it, e.g. "Toobit rejected the key. Check it has futures permission."

## 4. Testing and verification

- New `tests/test_ui_ids.py`, written first: collects every `getElementById("…")` id and every `data-at` / `data-pos` value used in `web/app.js` and `web/js/*.js`, and asserts each exists in `web/index.html`. Catches ids dropped by the merge that JS still uses.
- Existing test suite stays green (42 tests).
- Browser check after each implementation phase, at 1440, 1024 and 390px widths:
  - no console errors;
  - all three dialogs open and close;
  - Analyze updates the verdict and needle;
  - simulated position fills the position card, Close position works;
  - auto-trader block shows state (not armed during testing);
  - no horizontal page scroll; header does not overflow.

## 5. Phases

1. Tokens, fonts, base styles, chart colours.
2. Header, indicator strip, merged sidebar, JS mode removal.
3. Dialogs, history drawer, tablet and phone layouts.

## Out of scope

New features (PnL chart, order book, alerts), backend changes, framework migration, a light theme.
