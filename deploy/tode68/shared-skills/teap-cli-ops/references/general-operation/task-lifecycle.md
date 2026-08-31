# 任务生命周期

## 目录

- 对象边界
- 启动任务
- 查询与等待
- 取消任务

## 对象边界

`case/.tc` 是可编辑输入，`task` 是一次执行记录，`result/.tr` 是完成后的输出。

“计算完成的算例”“最新完成”“结果”“报告”必须从 task/result 路由。先查 `task status`，选择完成且有 `result_path` 的记录；不要从 `case list` 猜哪个 case 已运行。

## 启动任务

`task start` 按 source 类型执行两种明确行为：

- 一个或多个正整数 task ID：一次请求启动未开始任务或重启已修复的失败任务，复用原 ID、job type 和提交参数，不创建 task，也不下载/复制 `.tc`；计算中或等待中的 task 作为幂等项，不重复提交。
- case/result 服务端路径：按指定 `--job-type` 创建新 task；只有需要独立执行记录时才使用此形式。

原地启动已有任务：

```bash
teap task start 4776
teap task start 4776 4777 4778
```

ID 形式不接受 `--job-type`、`--note`、`--chronology-reduction-method` 或 job config，也不能混入路径或重复 ID。CLI 会在写入前检查整批目标：任一任务不存在、停止中、已完成或状态不允许时整批不提交；失败任务必须逐项完成 `log analyze` 和修正，不能把未改变的失败任务加入批次。成功后读取 `submitted_task_ids`、`already_active_task_ids` 和 `tasks`，确认后端没有静默忽略任何目标。

从服务端 case 路径创建新任务：

```bash
teap task start "$CASE_PATH" --job-type 4 --note "华中P50分析"
```

启动前按 [SKILL.md 的校验节奏](../../SKILL.md#校验节奏) 完成一次提交预检（`validate_case_for_submission.py`）；CLI 提交 `.tc` 时也会执行服务端 case 校验，通过后直接创建任务。

单个或批量 ID、路径形式均可同步等待，例如：

```bash
teap task start 4776 --wait
teap task start 4776 4777 --wait
```

完整 job type、算法配置名、输入类型、启动入口和模式专属前置条件见 [computation-modes.md](computation-modes.md)。选择或解释类型前必须读取该文档。特别注意：普通 `task start` 不支持启动 `104` 短路计算；不要仅因 `task job-types` 列出该类型就提交 `.tc`。

不要假设每个 case 支持全部类型。用户未要求执行时，不要擅自启动耗时任务，也不要在某个类型失败后改用名称相近的类型。

查看 CLI 当前识别的 job type 和来源：

```bash
teap task job-types
```

高级任务配置：

- `--note <text>`：备注；
- `--chronology-reduction-method <value>`：时序削减方法；
- `--job-config-json/--job-config-file`：任务级配置；
- `--poll-interval`：等待轮询间隔；
- `--timeout`：任务等待超时。

任务配置不应替代 case parameter 的持久设置，除非后端明确把该字段定义为任务级覆盖。

## 查询与等待

列任务（默认同时返回未完成和已完成页）：

```bash
teap task status
```

分页、过滤和最新完成结果：

```bash
teap task status --finished-only --finished-page -1 --finished-page-size 1
teap task status --finished-only --finished-page 3 --finished-page-size 20
teap task status --unfinished-only --filter demo --job-type 4
```

查单个任务：

```bash
teap task status --task-record-id 12345
task status 12345  # 错误语法；单任务查询必须使用 --task-record-id
```

等待已有任务：

```bash
teap task wait 12345 --timeout 3600 --poll-interval 2
```

等待失败时先读结构化 `failure.findings` 和 `failure.next_commands`。`task_failed` 不可原样重试。批量“开启/暂停仿真”即为 ID 形式的批量 `task start` 与 `task cancel`。

## 取消任务

“取消算例计算”指取消 `task`，不是删除 case 或 result。先检查单任务状态：

```bash
teap task status --task-record-id 12345
```

用户明确要求取消后执行：

```bash
teap task cancel 12345 --confirm
```

可一次取消多个等待中或计算中的任务。等待中会退出队列，计算中会进入停止中并向求解过程发送协作停止信号；市场模拟 job type 301 也使用该停止链路。返回成功不表示进程已经瞬时退出，应继续查询状态，直到离开等待中、计算中或停止中。

`task_not_cancellable`、`confirmation_required` 都是不可原样重试错误。已经停止中时只监控状态；已经完成时读取或删除结果；已经失败时分析日志。不要改用 `case delete` 或 `result delete` 强行停止活动任务。批量取消多个任务时，先逐项确认状态再一次性传入多个 task ID。
