# 复制算例与方案分叉

`case duplicate` 等于在服务端复制一个文件：源是 task ID（自动取其 `.tc` 输入或完成 `.tr` 的内嵌输入）或服务端 `.tc/.tr` path，产物是一个新命名的可编辑 `.tc`，直接落在服务端算例存放目录并出现在 `case list` 中。全过程不下载、不经过 XLSX、不创建 task。

```bash
BASE=$(teap case duplicate --task-record-id 4884 --name plan-base --operation-key task-4884-base --confirm)
# => {"name": "plan-base.tc", "path": "/srv/teapcase/plan-base.tc", "type": "tc", "editable": true, ...}

# 以复制结果为源继续分叉
teap case duplicate --file-path /srv/teapcase/plan-base.tc --name plan-p0 --operation-key task-4884-p0 --confirm
teap case duplicate --file-path /srv/teapcase/plan-base.tc --name plan-p1 --operation-key task-4884-p1 --confirm
```

规则只有四条：

1. `--name` 必填，就是新算例的文件名。与已有算例重名时返回 `case_duplicate_name_occupied`（`retryable=false`），换一个 `--name` 重跑即可，`--operation-key` 可复用。
2. 每个预期副本使用唯一 `--operation-key`；同一副本的原样重试必须复用原 key，成功后的重放直接返回缓存结果，不产生第二次写。
3. `case_duplicate_outcome_unknown` 表示服务端写入结果无法证明：停止一切等价重试，按 hint 只读核对 `case list --filter <name>` 后再决定。
4. 批量复制同一源时先复制一次（或先用 `task status --task-record-id <id>` 解析出源 path），后续统一用 `--file-path`；避免每次 `--task-record-id` 都拉取大分页任务表。

取得 `.tc` `path` 后按正常建模流程修改（sheet/row/parameter），并在启动任务前按 [SKILL.md 的校验节奏](../../SKILL.md#校验节奏) 做一次提交预检。

禁止用 `result download-tc`、本地 `cp` 或重新上传来完成复制或分叉；只有用户明确要求本地文件时才下载。
