# Phase 2A backend migration architecture

Status: **approved on 2026-07-28**

This directory is the versioned, approved architecture baseline for Phase 2B.
It was prepared against ProgTrack branch `Phase-0.1.2`, clean source/data
commit `3fc22583799b6ed394544035f1387e1c759c3aea`.

Contents:

- documents `00`–`10`: review index, findings, plugin/storage matrix,
  canonical entity model, backend/transaction/lock contract,
  managed-document/interchange contract, approved decisions, Phase 2B plan,
  evidence register, critical re-review resolution, and concrete
  path/callable reconciliation;
- `phase2a_readonly_audit.ps1`: reproducible read-only verifier, version
  `1.0.1`;
- `phase2a_audit_result_clean.json` and its `.sha256`: final clean-baseline
  result.

The final verifier result has SHA-256
`193ac3c6b09b55350daeba07ffc3c6015c04880d98151dda7911d71dcb34ba2e`,
exit code `0`, and `passed = true`. The repository Git status was empty before
and after verification.

Phase 2B Issues #49–#53 must implement this baseline. Later implementation
discoveries may add reviewed amendments, but must not silently contradict this
approved contract.
