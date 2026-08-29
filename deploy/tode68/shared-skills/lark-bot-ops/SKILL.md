---
name: lark-bot-ops
description: Operate Feishu and Lark resources with bot-scoped tooling only, checking bot capability first and refusing insecure fallbacks.
platforms: [linux]
prerequisites:
  commands: [lark-cli]
---

# Lark Bot Ops

Use this workflow for Feishu and Lark resources where execution identity matters, such as cloud docs, calendars, schedules, tasks, workflows, and similar objects.

## Hard Rule

Use bot-scoped tooling only.

- Prefer `lark-cli`.
- Use corresponding Lark skills only if they also execute as bot identity.
- Do not use user identity.
- Do not use plugin flows that require user authorization and then inherit user permissions.

## Execution Order

1. identify the target Feishu resource and the required action
2. confirm the action can be done through `lark-cli` or a bot-scoped Lark skill
3. check whether the needed bot capability exists before attempting the action
4. execute with bot identity only
5. verify the result

## Capability Check

Before acting, confirm at least these points:

- the required bot-facing tool or command exists
- the target resource type is supported by bot scope
- the intended action is supported by bot scope
- the action does not require inheriting a user's authority

If any of these checks fail, stop before execution.

## Failure Rule

If the task cannot be completed with bot-scoped capability:

- do not fall back to user-authorized access
- do not ask for user login or delegated user permission as a shortcut
- report that the required bot capability is missing

## Response Pattern

When blocked, say the capability boundary plainly:

- what resource or action was requested
- that only bot-scoped execution is allowed in this shared Hermes service
- which bot capability is missing or unverified
- that you will not fall back to user-authorized access

## Examples Of In-Scope Work

- reading or writing a Feishu cloud document through bot tooling
- checking or updating calendar items through bot tooling
- operating task objects through bot tooling

## Anti-Patterns

- using a user token, user session, or inherited user permission
- silently switching from bot tooling to a plugin with user authorization
- attempting the action first and checking bot capability only after it fails
