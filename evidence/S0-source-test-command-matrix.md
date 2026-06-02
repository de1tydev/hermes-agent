# S0 Source And Test Command Matrix

Stage: `S0-evidence-boundary`

| Surface | Source / Runtime | Commands | S0 Decision |
| --- | --- | --- | --- |
| Hermes worktree | `/home/liao/.hermes/hermes-agent` | `scripts/run_tests.sh`; focused S0 gate: `python scripts/validate-s0-capability-manifest.py evidence/S0-capability-manifest.json` | Runtime behavior unchanged; S0 runs evidence gates only. |
| Hermes Hindsight provider | `plugins/memory/hindsight/__init__.py`, `agent/memory_provider.py`, `agent/memory_manager.py`, `hermes_cli/config.py` | Existing pytest suite via `scripts/run_tests.sh`; no new runtime tests in S0 | Lifecycle and schema frozen as evidence. |
| OpenClaw Hindsight source package | `/home/liao/code/hindsight/hindsight-integrations/openclaw` | `npm run build`; `npm run test`; `npm run test:integration`; `npm run prepublishOnly` | Use this as S4 implementation target. |
| OpenClaw installed runtime | `/home/liao/.openclaw/npm/node_modules/@vectorize-io/hindsight-openclaw` | Runtime inspection only | Do not patch installed dist as deliverable. |
| OpenClaw CLI/runtime | `/home/liao/.local/bin/openclaw`; observed `OpenClaw 2026.5.19 (b6d41a5)` | Runtime smoke may use OpenClaw after S4/S5 wiring | S0 is read-only. |

## S0 Verification Commands

Required S0 gates:

```bash
python scripts/validate-s0-capability-manifest.py evidence/S0-capability-manifest.json
python3 -m json.tool evidence/S0-capability-manifest.json >/dev/null
test -s evidence/S0-openclaw-capability.md && test -s evidence/S0-hermes-capability.md && test -s evidence/S0-current-config.md && test -s evidence/S0-source-test-command-matrix.md
# Privacy/canary scan from the S0 handoff prompt. Do not inline the full
# scanner here because the scanner pattern intentionally flags itself.
git diff --exit-code -- run_agent.py model_tools.py agent/memory_manager.py agent/memory_provider.py plugins/memory/hindsight/__init__.py hermes_cli/config.py gateway tools toolsets.py
```
