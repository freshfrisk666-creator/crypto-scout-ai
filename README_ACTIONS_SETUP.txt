CRYPTO SCOUT AI — GITHUB ACTIONS + DASHBOARD PACKAGE

WHAT IS INCLUDED
- .github/workflows/crypto-scout.yml : one scheduled workflow
- scripts/run_actions_cycle.py : monitors positions every run and scans once per hour
- scripts/build_dashboard.py : exports public dashboard data from the local SQLite paper DB
- dashboard/index.html, style.css, app.js : static dashboard
- data/actions_state.json : persisted timestamp of the last successful scan

INSTALL
1. Extract/merge this ZIP into the ROOT of:
   C:\Users\danie\Downloads\crypto-scout-ai
   Keep the folder structure exactly as shown. Do not extract inside a nested extra folder.

2. Confirm your local, already-tested versions of these files are present:
   - database/db.py
   - portfolio/paper_trading.py
   - runner/crypto_scanner.py
   - runner/crypto_monitor.py
   - the tests you have just run successfully

3. In PowerShell:
   cd C:\Users\danie\Downloads\crypto-scout-ai
   python -m pytest -q
   python -m py_compile scripts\run_actions_cycle.py scripts\build_dashboard.py

4. Commit and push all pending project changes. Suggested commands:
   git status --short
   git add .github/workflows/crypto-scout.yml scripts/run_actions_cycle.py scripts/build_dashboard.py dashboard/index.html dashboard/style.css dashboard/app.js data/actions_state.json
   git add database/db.py portfolio/paper_trading.py runner/crypto_scanner.py runner/crypto_monitor.py tests
   git add -f data/crypto_scout.db
   git commit -m "Add GitHub Actions paper engine and dashboard"
   git fetch origin
   git pull --rebase origin main
   git push origin main

   NOTE: if the local database file does not exist, do not run the force-add line;
   the Actions workflow will create and persist it on its first run. If it does exist
   and is clean (10,000 USDT, zero positions), it can be included. Review git status
   first and do not overwrite a newer paper database downloaded from GitHub.

5. On GitHub, open:
   Repository -> Settings -> Pages -> Build and deployment -> Source
   Select "GitHub Actions" and save.

6. Open the Actions tab, select:
   "Crypto Scout AI - Paper Engine and Dashboard"
   Click "Run workflow" on branch main.

HOW IT RUNS
- Each scheduled run monitors open positions with the 5-minute timeframe.
- The scanner runs on the first/manual execution and then when at least 60 minutes have
  elapsed since the last successful scan.
- The dashboard is deployed on manual/scan runs, if a position closes, and roughly every
  15 minutes. The schedule is best-effort; it is not real-time infrastructure.
- The SQLite database and last-scan timestamp are committed to main only when persistent
  state changes, so open/closed positions and virtual cash survive ephemeral GitHub runners.
- The dashboard JSON is generated for the Pages artifact and is not committed on every
  monitoring cycle.
- This requires Actions to have permission to write repository contents. If repository
  settings disable Actions write permissions, enable read/write permissions for workflow
  tokens under Settings -> Actions -> General.

SAFETY
- The workflow checks paper_trading_only=true, live_orders_enabled=false, and
  leverage_enabled=false before running the cycle.
- Only public market-data calls are used. No Binance private API keys or real order
  endpoints are used.
- Equity and unrealized P&L on the dashboard are estimates, not executable prices or
  guaranteed fill values.
