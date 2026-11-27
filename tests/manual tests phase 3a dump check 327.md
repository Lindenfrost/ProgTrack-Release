# Specialist manual check — Windows fault-dump attribution for #327

Separate from ordinary Phase 3A. Requires WinDbg or CDB on a disposable Windows test client using the exact candidate package.

Never publish process dumps or unredacted logs; they may contain credentials, database settings, session tokens, paths, or animal data.

## Before starting

- Candidate version and SHA-256:
- Windows edition/build:
- Debugger/version:
- Date/time and timezone:
- No other ProgTrack/Launcher process remains: yes/no

## Steps

1. Close ProgTrack normally and confirm no Launcher.exe or ProgTrack process remains. Start exactly one candidate under WinDbg/CDB.
2. Confirm launcher_fault_<PID>.log records timestamp, PID, launcher version, and executable path.
3. Configure first-chance breaks for 0x8001010d and 0x80040155. Reproduce separately: Shared connection, valid login, invalid login, cancellation, and successful login.
4. For each exception, record timestamp, PID, thread ID, code, first/second-chance status, native module and stack, UI action, and whether the process terminated. Do not infer the native caller from Python traceback frames alone.
5. If no exception occurs, record the actions completed. This does not explain historical dumps.
6. If a crash dump is necessary, create a restricted minimal dump locally, inspect it locally, and remove temporary dump settings/artifacts. Do not upload it.

[ ] Specialist check result
# Comments:

## Evidence summary

- Exceptions observed:
- Native module/interface identified:
- Did the process terminate:
- Reproduction action:
- Follow-up issue/update:
- Sensitive artifacts kept out of published evidence: yes/no
