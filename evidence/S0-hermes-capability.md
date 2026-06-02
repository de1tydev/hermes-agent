# S0 Hermes Capability Evidence

Stage: `S0-evidence-boundary`

## Repo And Runtime

- Hermes worktree: `/home/liao/.hermes/hermes-agent`
- Branch: `local/v0.15-main-runtime`
- Baseline commit before S0 edits: `14883e5a9bfdae9dfc440d77ddb2282056feb951`
- CLI version command observed: `Hermes Agent v0.15.2 (2026.5.29.2)`
- Current provider: `memory.provider: hindsight`

## Memory Provider Lifecycle

Source evidence:

- `agent/memory_provider.py` defines the provider contract:
  `initialize`, `system_prompt_block`, `prefetch`, `sync_turn`,
  `get_tool_schemas`, `handle_tool_call`, `shutdown`, and optional hooks.
- `agent/memory_manager.py` registers at most one external provider, builds
  prompt blocks, runs prefetch, queues next-turn prefetch, syncs completed
  turns, routes memory tools, handles session switches, compression hooks,
  memory-write mirroring, delegation hooks, and shutdown.
- `plugins/memory/hindsight/__init__.py` registers
  `HindsightMemoryProvider()` through the memory plugin context.

Key Hindsight provider behavior:

- `is_available()` is config/dependency based and does not make network calls.
- `initialize()` reads `$HERMES_HOME/hindsight/config.json`, resolves
  mode/api_url/bank/budget/memory mode, and initializes retain/recall controls.
- `prefetch()` returns cached background recall text with a Hindsight header.
- `queue_prefetch()` runs recall/reflect in a background thread unless disabled
  by memory mode, `auto_recall`, or shutdown state.
- `sync_turn()` accumulates full session turns and enqueues retain on the
  configured cadence using a single writer queue.
- `on_session_switch()` flushes buffered old-session turns and resets
  session-local document/counter state.
- `shutdown()` stops retain writes and drains background work.

## Config Schema

Schema evidence from `plugins/memory/hindsight/__init__.py:get_config_schema()`:

- Modes: `cloud`, `local_embedded`, `local_external`
- Secret fields: `api_key`, `llm_api_key`
- Core fields: `api_url`, `llm_provider`, `llm_base_url`, `llm_model`,
  `bank_id`, `bank_id_template`, `bank_mission`, `bank_retain_mission`,
  `recall_budget`, `memory_mode`, `recall_prefetch_method`, `retain_tags`,
  `retain_source`, `recall_tags`, `recall_tags_match`, `recall_types`,
  `auto_recall`, `auto_retain`, `retain_every_n_turns`, `retain_async`,
  `retain_context`, `recall_max_tokens`, `recall_max_input_chars`,
  `recall_prompt_preamble`, and `timeout`.

Default config evidence from `hermes_cli/config.py`:

- `memory.memory_enabled` defaults to true.
- `memory.provider` defaults to empty and activates one external provider when
  set, e.g. `hindsight`.

## Test Entry Points

S0 resolved the repo-level test runner:

- Preferred full project gate: `scripts/run_tests.sh`
- Hindsight plugin focused tests are under `tests/` and
  `plugins/memory/hindsight/`; S0 did not add runtime behavior or execute a
  full suite.

S0 verification is intentionally scoped to the evidence validator and
non-regression checks listed in `evidence/S0-source-test-command-matrix.md`.
