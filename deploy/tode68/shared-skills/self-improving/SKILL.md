---
name: self-improving
version: 2.0.0
author: Hermes Agent
license: MIT
description: 将用户纠正和可复用经验写入当前 Hermes Profile 的内置 Memory。
platforms: [linux]
metadata:
  hermes:
    tags: [Learning, Reflection, Memory]
    related_skills: []
---

# Self Improving

## When to Use

仅在用户明确纠正了事实或工作方式、工具执行暴露了可复用故障规律，或者用户明确要求记住一条长期规则时使用。

## Profile Boundary

- 长期规则只通过 Hermes 内置 `memory` 工具写入当前 Profile。
- 禁止用 terminal 或文件工具绕过内置 Memory 的 Profile 边界。
- 禁止读取或写入其他 Profile 以及任何共享学习目录。
- 不使用 Hindsight 或任何外部 Memory provider。

## Write Rules

1. 先检查现有 Memory，避免重复和矛盾条目。
2. 只保存稳定、可复用、未来仍有价值的结论；不要保存聊天记录、临时状态或敏感凭据。
3. 内置 Memory 保持简短，使用一条清晰规则替换多条重复描述。
4. 不得自动修改人格、工作区指令、心跳任务或 Gateway 设置。
5. 写入后重新读取目标文件，确认内容只落在当前 Profile。

## Failure Learning

工具或命令失败但尚未形成稳定规则时，只在当前任务中保留诊断；形成可复用结论后再通过内置 `memory` 工具保存。条目包含日期、症状、根因、修复和验证方法。不要记录 token、Cookie、Authorization header、用户隐私或完整聊天内容。
