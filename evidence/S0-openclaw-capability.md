# S0 OpenClaw Capability Evidence

Stage: `S0-evidence-boundary`

## Source And Runtime

- Runtime package: `/home/liao/.openclaw/npm/node_modules/@vectorize-io/hindsight-openclaw`
- Runtime dist entry: `/home/liao/.openclaw/npm/node_modules/@vectorize-io/hindsight-openclaw/dist/index.js`
- Runtime package version: `0.7.7`
- Source repo root: `/home/liao/code/hindsight`
- Source package cwd: `/home/liao/code/hindsight/hindsight-integrations/openclaw`
- Source package version: `0.8.0`
- Source branch: `fix/openclaw-retain-context`
- Source commit: `bea43385c60eaf1c374b06eebd5515dc625f5000`
- Source remote: `origin=https://github.com/vectorize-io/hindsight.git`, `fork=https://github.com/de1tydev/hindsight.git`

The inspected source package version does not match the installed runtime package
version. S4 must implement against the source package and then build/package it;
the installed `node_modules` dist is runtime evidence only.

## Package Commands

From the source package `package.json`:

- Build: `npm run build`
- Unit tests: `npm run test`
- Integration tests: `npm run test:integration`
- Publish preflight: `npm run prepublishOnly`

The package build output is `dist/` under the source package. No automatic
runtime-dist sync was found in S0.

## Hook Boundary

Source evidence from
`/home/liao/code/hindsight/hindsight-integrations/openclaw/src/index.ts`:

- Registers OpenClaw hooks: `before_dispatch`, `before_prompt_build`,
  `agent_end`, and `session_end`.
- `before_dispatch` caches stable identity before later recall/retain hooks.
- `before_prompt_build` performs recall, applies ignore/stateless filters,
  resolves identity, derives `bankId`, composes the recall query, and injects
  Hindsight memory context.
- Shared retain path is used by `agent_end` and `session_end`.
- `session_end` is a force flush for un-retained tail turns.
- Identity skip returns retryable or final skip reasons for missing provider,
  missing sender identity, operational sessions, temp sessions, and direct-chat
  mismatches.
- Stateless behavior is controlled by `statelessSessionPatterns` and
  `skipStatelessSessions`.

Line references captured in raw SDE evidence:

- `deriveBankId`: `src/index.ts:1133-1192`
- identity skip and operational filters: `src/index.ts:1040-1105`
- plugin service start and LLM config detection: `src/index.ts:1595-1620`
- hook registration: `src/index.ts:1981-2034`, `src/index.ts:2603-2611`
- stateless recall/retain filters: `src/index.ts:2046-2068`,
  `src/index.ts:2318-2342`
- retain cadence/window: `src/index.ts:2408-2473`

## LLM Helper Decision

Decision: `missing_stable_plugin_llm_helper`.

Evidence:

- The package has `detectLLMConfig()` and daemon bootstrap settings for Hindsight
  local mode.
- Current runtime config uses an external Hindsight API URL, so the plugin does
  not need local LLM credentials for existing retain/recall behavior.
- No stable OpenClaw plugin API helper for arbitrary model calls was identified
  in S0. The opt-in `registerTool` path exposes Hindsight knowledge tools to
  agents; it is not a plugin-side summary generator helper.

Downstream rule:

- S4/S5 must not hardcode a private model path or assume OpenClaw can run a
  real summary generator inside the plugin. If S4 needs generator behavior on
  the OpenClaw side, use a no-op/fake generator or trigger correct-course for a
  core/API helper plan.
