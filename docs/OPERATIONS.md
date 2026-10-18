# VPS operations (Windows VPS, demo account)

> **DEMO ONLY.** `InpDemoOnly=true` and `InpAllowLive=false` are the defaults. Live order
> execution needs `InpDemoOnly=false`, `InpAllowLive=true` **and** `InpLiveConfirm=I_ACCEPT_LIVE_RISK`.
> Do not change these without a separate, explicit decision after a documented demo evaluation.

Nothing on this page has been executed on your VPS yet. See README → "Verification status".

## 0. Inspect first (read-only)

1. `powershell -ExecutionPolicy Bypass -File deploy\windows\inspect-vps.ps1 > inspect-vps.txt`
   collects the OS, time sync, installed and running terminals, the logged-on session, Python and ports.
2. In MT5, open the target symbol chart and run **Scripts → LRB → LRB_InspectEnvironment**. It
   writes `<Common>\Files\LRB\inspect_<SYMBOL>.json` with the broker, account currency, demo/real
   status, **hedging mode**, contract size, tick size and tick value, volume limits and step, margin
   (1 lot), stop-out level, execution and filling modes, swaps, trade sessions and tick-history availability.
3. Proceed only if `margin_mode = RETAIL_HEDGING` and `account_trade_mode = DEMO`.
4. Optional: `python -m lrb.tools.spec_from_inspect inspect_EURUSD.json symbol_spec.json` converts
   the inspection into a SymbolSpec for `lrb-backtest --symbol-spec`. On Windows,
   `python -m lrb.tools.inspect_mt5 --symbol EURUSD` collects the same facts through the official
   MetaTrader5 package; it is read-only and never sends orders.

## 1. Install

Prerequisites: MT5 installed and logged in to a **demo** hedging account (tick "Save password"),
Python 3.11 (`py -3.11`), and git.

```powershell
git clone <this repo> C:\LRB\TRading
cd C:\LRB\TRading
# MQL5 sources -> MT5 data folder (MT5: File > Open Data Folder)
powershell -ExecutionPolicy Bypass -File deploy\windows\install-mql5.ps1 -Mt5DataDir "C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\<id>"
# Python service (creates venv, hash-pinned deps, HMAC secret, scheduled tasks, firewall rule)
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
# Time sync (Administrator)
powershell -ExecutionPolicy Bypass -File deploy\windows\time-sync.ps1
# Smoke test without MT5
$env:LRB_HMAC_SECRET_FILE="$env:APPDATA\MetaQuotes\Terminal\Common\Files\LRB\hmac_secret.txt"
python\.venv\Scripts\python.exe -m lrb.tools.bridge_emulator --url http://127.0.0.1:8787
```

Compile in MetaEditor (F7): `Experts\LRB\LRB_EA.mq5`, `Experts\LRB\LRB_AIBridge.mq5`, and
`Scripts\LRB\*.mq5`. **The MQL5 code has not been compiled in the development environment.**
Fix any compiler messages before attaching anything.

## 2. Demo setup in MT5

1. *Tools → Options → Expert Advisors*: enable *Allow algorithmic trading*, and add
   `http://127.0.0.1:8787` to *Allow WebRequest for listed URL*.
2. Chart 1: EURUSD **M15**. Attach `LRB_EA` and load `Presets\LRB_EA_demo.set`. Start with
   `InpAIMode=AI_OFF` (strategy A).
3. Chart 2: any symbol. Attach `LRB_AIBridge` with the same `InpMagic`. It is needed for telemetry
   even with the AI off.
4. Check the *Experts* tab for `LRB INIT … demo=1 hedging=1 permitted=1` and `RECONCILED`.
5. Dashboard (on the VPS): `http://127.0.0.1:8787/dashboard`.
6. *File → Profiles → Save As → `LRB`*.
7. Optional strategy B: set `InpAIMode=AI_FILTER`. The service uses `rules-v1` until an
   out-of-sample-validated model is configured with `LRB_MODEL_PATH`.

## 3. Keeping MT5 running (separate from Python supervision)

The Python service is supervised by Task Scheduler (`LRB-Service`, restarted every minute on
failure; `LRB-Health` every 5 minutes). **That does not keep MT5 alive.** The terminal is a
desktop application:

* Configure automatic logon for the trading user, for example Sysinternals *Autologon*, which
  stores the password LSA-encrypted. Do it manually; nothing here downloads it.
* Run `deploy\windows\mt5-autostart.ps1 -TerminalPath "C:\Program Files\MetaTrader 5\terminal64.exe"`
  to start the terminal with profile `LRB` at logon.
* **Disconnect** RDP sessions by closing the window. Do not choose *Sign out*: signing out kills
  the terminal.
* After a reboot, verify that Algo Trading is on, both EAs are attached, and the journal shows `RECONCILED`.
* `LRB-Health` writes Windows Application events: 1001 service restarted, **1002 terminal not
  running**, 1003 EA status stale, 1004 status missing, 1005 bridge heartbeat stale. Hook these up
  to whatever alerting you use.

## 4. Restart and reconciliation behaviour

On every start the EA is in `RECONCILE`. It waits for the broker connection and deal history,
then compares its persisted state with **actual positions** (magic + symbol). It never opens a
basket on top of existing positions, adopts positions as an orphan basket when the local state is
missing (no new legs; money, time and guard exits active), and finishes closing a basket that was
in `CLOSING`. See STRATEGY_SPEC §8.

## 5. Logs, storage and backups

* EA: `<Common>\Files\LRB\<magic>\journal_<date>.csv` (UTC), `baskets.csv` (final realized P&L
  per basket), `state.txt` and `status.json`. The MT5 *Experts* log shows the same `LRB …` lines.
* Service: `C:\LRB\data\logs\service-YYYYMMDD.log` (JSON, UTC, secrets redacted, 30-day retention)
  and the SQLite database `C:\LRB\data\lrb_service.db` (WAL, versioned migrations applied at
  start, retention pruning).
* Backups: `LRB-Backup` runs daily at 00:30 local time. It makes an online SQLite backup plus
  copies of the EA state, journal and status files, keeping 14. The HMAC secret is **not** backed
  up; regenerate it if lost (delete the file, re-run the installer, restart both EAs).

## 6. EMERGENCY SHUTDOWN

In order of preference:

1. **Flatten through the EA** (the terminal must be running and connected):
   `powershell -ExecutionPolicy Bypass -File C:\LRB\TRading\deploy\windows\emergency-stop.ps1 -Mode FLATTEN`
   Alternatively, in MT5, run *Scripts → LRB → LRB_KillSwitch* with `InpMode=KILL_FLATTEN`.
   The EA closes **only its own basket positions** and halts. Confirm on the dashboard, or in the
   MT5 *Trade* tab, that the positions are gone.
2. **Stop new exposure only**: use `-Mode HALT` (or `KILL_HALT`). Exits stay active.
3. **If the EA or terminal is not working**: close the positions manually in the MT5 *Trade* tab,
   or from the broker's web or mobile terminal. Then disable *Algo Trading* in MT5.
4. Stop everything: remove both EAs from their charts and stop `LRB-Service` (`-StopService`).

To clear afterwards (only after review): run `emergency-stop.ps1 -Mode CLEAR` **and** `LRB_KillSwitch`
with `KILL_NONE`. A file-based kill takes precedence over the global variable while the file exists.
A drawdown halt additionally requires `InpResetDrawdownHalt=true` for one restart, then set it back
to `false`.

## 7. Separate Linux service host (option B)

`sudo deploy/linux/install.sh /path/to/TRading <WINDOWS_VPS_IP>` installs the service as the
system user `lrb` with systemd hardening, daily backups, NTP and ufw rules. Put Caddy in front of
it (`deploy/linux/Caddyfile.example`) for TLS on 443, restricted to the Windows VPS IP. Copy the
HMAC secret over a secure channel to `/etc/lrb/hmac_secret`. In the bridge, set
`InpServiceUrl=https://<host>` and allow that URL in MT5. The MT5 terminal must still run on Windows.
