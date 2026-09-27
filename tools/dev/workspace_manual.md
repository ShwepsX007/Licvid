# Multi-chart Workspace — manual checklist

## MVP

1. **Add 3 panels, reload — restored**
   - Open `/terminal` (demo mode: LIQSCOPE_DEMO=1)
   - Workspace toolbar appears above chart (if not, check console)
   - Click "+ Add chart" twice → now 4 panels (2 initial + 2 added) in 2x2 grid
   - Change symbols: first panel BTC, second ETH, third SOL, fourth XRP via dropdown
   - Change timeframe for one panel to 1h
   - Toggle layers: in panel header click "☰ Слои" → enable "🎯 Уровни (оценка)" for one panel, disable for another
   - Reload page (F5) → panels count, symbols, timeframe, layers should restore from localStorage `liqscope_terminal_workspace_v1`
   - Check localStorage: `JSON.parse(localStorage.getItem('liqscope_terminal_workspace_v1'))` has 4 panels with correct symbols

2. **Detach one panel — open, change symbol — main sees change**
   - In first panel header click detach button "⧉"
   - New window/tab opens with URL `/terminal?mode=panel&panel=<id>&workspace=<wsId>&symbol=...`
   - In detached window only that panel visible (panel-mode body class), with "↩ Attach back" button
   - Change symbol in detached window (dropdown) → in main window, BroadcastChannel should update that panel's symbol (check console, panel header updates)
   - Alternatively change symbol in main → detached updates (via PANEL_STATE_UPDATE)

3. **Attach back — panel returned**
   - In detached window click "↩ Attach back" → window closes, main window panel loses "detached" dashed style
   - If user just closes detached window (X) → main window auto-detects via polling `win.closed` every 1s and marks panel as attached (detached style removed)

4. **Reorder via tabs**
   - Drag tab of panel 2 onto tab of panel 1 → grid order changes
   - Reload → order preserved

5. **Per-panel layers independent**
   - Enable levels for BTC panel, disable for ETH panel
   - Check canvases: BTC shows liquidation levels, ETH doesn't
   - Reload → layers state preserved per panel

6. **Mobile**
   - Resize to 375px width → grid becomes 1 column, tabs scrollable, only active panel highlighted
   - Click tab to switch active panel
   - No layout break, no horizontal overflow

7. **Performance**
   - Max 6 panels enforced, alert when exceeding
   - One WS per window (main + each detached), not per panel
   - Detached panel mode loads faster: check network — only minimal HTML, no full terminal feed (still loads but hidden via CSS)
   - No quadratic redraws: each panel has own chart and canvas, queueRedraw per panel

8. **Security**
   - Try to inject XSS via symbol param: `/terminal?mode=panel&panel=<id>&symbol=<script>alert(1)</script>` → should be rejected by _validateSymbol, fallback to BTC_USDT
   - BroadcastChannel messages validated: panelId regex `^[a-zA-Z0-9_-]{1,64}$`, symbol regex `^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$`
   - No innerHTML with unescaped data: use _esc() for tab labels

## Dev script

```bash
LIQSCOPE_DEMO=1 python3 server.py
# open http://127.0.0.1:8000/terminal
# run automated checks:
node tests/workspace_manager.js
node tests/workspace_e2e.js http://127.0.0.1:8000
```

## DoD

- [x] 2-4 panels in grid, responsive
- [x] Reorder via tabs drag&drop
- [x] Detach to separate window, attach back
- [x] Per-panel toggles for levels and alert
- [x] Workspace saved in localStorage, restored after reload
- [x] One WS per window, max panels limit
- [x] Panel mode loads faster (CSS hides heavy blocks)
- [x] Phrase removed from cabinet i18n in all languages
