# S0 Current Config Snapshot

Stage: `S0-evidence-boundary`

All values below are redacted or non-secret. Raw redacted snapshots and hashes
are stored under the SDE artifact root:

`projects/hindsight-session-summary-eps/sde-state/artifacts/hindsight-session-summary/S0-evidence-boundary/raw-evidence/`

## OpenClaw

- Config path: `/home/liao/.openclaw/openclaw.json`
- Raw config SHA-256: `a7a639d5c507ebbd3300148ac7453f0b51cb3e6099a4852e63154de6d8dd365c`
- Redacted selected snapshot SHA-256:
  `3f78528633a7a61a98ab3556c37baff74af219a1870a6f0fc76513b2b680d61f`
- Plugin entry: `plugins.entries.hindsight-openclaw`
- Hook access: `hooks.allowConversationAccess=true`

Current Hindsight OpenClaw values:

- `bankId`: `saber-prod`
- `dynamicBankId`: `false`
- `autoRecall`: `true`
- `autoRetain`: `true`
- `retainEveryNTurns`: `2`
- `retainOverlapTurns`: `2`
- `recallContextTurns`: `3`
- `recallMaxQueryChars`: `4000`
- `recallBudget`: `mid`
- `recallTopK`: `10`
- `recallTypes`: `observation`, `world`, `experience`
- `recallRoles`: `user`, `assistant`
- `retainRoles`: `user`, `assistant`
- `retainFormat`: `json`
- `retainSource`: `openclaw`
- `retainToolCalls`: `false`
- `skipStatelessSessions`: `true`
- `statelessSessionPatterns`: `agent:*:subagent:**`,
  `agent:*:heartbeat:**`, `agent:*:cron:**`
- `excludeProviders`: `heartbeat`, `cron`, `subagent`
- `hindsightApiUrl`: `http://127.0.0.1:8888`
- `enableKnowledgeTools`: `true`
- Secret-like fields and token-count fields in raw snapshots are stored only as
  `[REDACTED]`.

## Hermes

- Main config path: `/home/liao/.hermes/config.yaml`
- Hindsight config path: `/home/liao/.hermes/hindsight/config.json`
- Hermes `memory:` section redacted SHA-256:
  `45f3de95d1087b69c6112ac4308124faa4cf3812d0af46c05689509f647b3c28`
- Hermes Hindsight config redacted SHA-256:
  `a4505a7c6ec93d51e281a694e1c71f7193fd7b6e2bddf0915e1c17abb79555d9`
- `.env` checked only for key names; no values were copied.

Current Hermes values:

- `memory.memory_enabled`: `true`
- `memory.user_profile_enabled`: `true`
- `memory.memory_char_limit`: `2200`
- `memory.user_char_limit`: `1375`
- `memory.provider`: `hindsight`
- Hindsight mode: `local_external`
- Hindsight API URL: `http://127.0.0.1:8888`
- Hindsight bank: `saber-prod`
- Hindsight memory mode: `hybrid`
- Hindsight recall method: `recall`
- Hindsight recall input cap: `4000`
- Hindsight retain cadence: `2`
- Hindsight retain async: `true`
- Hindsight retain source: `hermes-agent`
- Hindsight retain tags: `source_system:hermes`
- Hindsight timeout: `120`

## Secret Handling

- No raw secret values are committed in S0 evidence.
- `.env` value capture was deliberately skipped.
- Redaction uses conservative key-name filtering for `api_key`, `token`,
  `password`, `secret`, `credential`, and `auth`.
