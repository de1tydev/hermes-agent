---
name: teap-cli-ops
description: 通过 `teap` CLI 操作 TEAP 电力系统规划与仿真平台。用于解释 TEAP，管理或删除 case/.tc，编辑设备表、参数与时序，执行预处理算法，启动、监控或取消 task，诊断失败日志，查询、导入、按用户明确要求导出或删除 result/.tr，以及按模板生成 Word 仿真分析报告。
platforms: [linux]
prerequisites:
  commands: [teap]
---

# TEAP CLI Ops

优先使用已安装的 `teap` 命令完成 TEAP3 算例建模、任务执行和结果分析。不要用 `curl`、临时 Python HTTP 请求或宿主专用路径绕开 CLI。

默认只操作服务端对象：算例建模、修改、复制、方案分叉、任务执行和结果分析均通过 `teap` 的服务端命令完成，不下载 `.tc/.tr`。只有用户明确要求取得、导出、交付、保存或在本地检查文件时，才执行任何 case/result/file download；结构化接口不足时应说明限制并询问用户是否需要本地文件，不得自动下载解析。

默认使用 `teap -o json ...`，只在人工浏览时使用表格输出，只在调试后端原始契约时使用 `-o raw-json`。

## 先路由对象

- 用户说“算例、模型、`.tc`、设备、参数、时序”时，按可编辑 `case` 处理。
- 用户说“运行、计算、任务、进度、失败”时，按一次执行记录 `task` 处理。
- `task start <task-id> [<task-id> ...]` 一次原地启动/重启一个或多个未开始或已修复的失败任务，保留 ID 和原提交参数；`task start <case-or-result-path> --job-type <id>` 才创建新 task。不要为原地启动下载或复制 `.tc`。
- 用户说“结果、报告、`.tr`、最新完成、计算完成的算例”时，按已完成 `task/result` 处理；不要用 `case list` 代替结果查询。
- 用户说“最新完成”时，只请求已完成任务的最后一页且每页一条：`teap -o json task status --finished-only --finished-page -1 --finished-page-size 1`；不要拉取完整任务列表。
- 用户说“取消、暂停、停止正在计算”时按 `task cancel` 处理；“删除输入模型”按 `case delete`；“删除已完成结果”按 `result delete`。三者不可互换。

需要平台和对象背景时读取 [references/teap-overview.md](references/teap-overview.md)。

## 执行纪律

1. 对新服务或认证状态未知的服务，先运行 `teap -o json auth status`。不要根据本地是否有 token 猜服务认证模式。
2. 查找已有算例，或创建/导入算例；记录服务端 case path。
3. 按依赖顺序串行编辑同一个 `.tc`：分区 -> 母线 -> 设备 -> 时序 -> 参数。创建或修改时序前必须运行 `teap -o json timeseries types <case-path> --sheet <device-sheet>`，只使用返回的 `types[].value` 和 `replace|multiply`；不得猜测设备表、类型键或数据合并方式。
4. 修改已有对象前读取相关 sheet/row；新建空算例可使用模板字段直接创建，再做一次定点校验。Core 字段名是 `value_type`；CLI 接受 `data_type` 作为输入别名，但不得同时给出冲突值。成功输出含 `timeseries_value_type_not_recommended` warning 时，先核对业务语义再决定是否保留该写入。
5. 启动任务前运行 `teap -o json case validate <case-path>`。
6. 任务失败时先逐项读取 `failure` 并运行 `log analyze --task-id <id>`；全部目标修正后可用一个 `task start <id> [<id> ...]` 批量原地重启，未修正时不得加入批次。
7. 结果查询必须按“最小分页 -> 动态发现 -> 精确 group/设备曲线与单位 -> 有界本地聚合”执行。先运行 `teap -o json result groups <id-or-result-path>`；设备曲线从 `curve_units` 选择精确单位，平衡表先查询一次并从 `available_units` 选择单位，再用 `--unit <exact-unit>` 取得已换算 JSON。不要因为猜测的 group 或单位失败就下载 `.tr`。
8. 结构化接口没有目标数据或无法解析结果时，报告已尝试的 group/curve 和具体限制；只有用户明确要求取得本地文件后才下载 `.tr`，不得自动转为本地解析。
9. 取消或删除前先读取目标状态，并且只有用户明确要求该破坏性操作时才添加 `--confirm`；不得把确认选项作为失败后的自动重试手段。
10. 复制任何已有服务端算例或历史 task 输入时只传 task ID 或服务端 path：`case duplicate` 返回非 `.tc` 时执行其 `next_action.command`，再用得到的服务端 `.tc` 分叉。不得下载、local copy、重新上传。

同一个 `.tc` 的写操作必须串行，row index 分配依赖当前文件状态。不要为了“确认”重复成功的写命令。

## 认证与凭据

按 `auth status` 的结构化字段判断：

- `auth_required=false`：允许匿名访问，继续操作。
- `auth_required=true` 且 `auth_token_valid=true`：凭据有效，继续操作。
- `auth_required=true` 且凭据无效：由宿主注入凭据，或让用户在受控终端登录。
- 探测失败：报告连接、TLS 或服务错误，不要归因于 token 缺失。

不得读取、打印或发送 `teap-auth.json`、`.teap/token`、`.config/teap-cli/config.json` 中的 token。不得要求用户在对话中粘贴 token。

## 失败处理

CLI 失败 JSON 包含：

```json
{"ok":false,"error":{"code":"...","message":"...","retryable":false,"hints":["..."]}}
```

- `retryable=false`：不要原样重试；执行 `hints` 中的诊断或修正命令。
- `retryable=true`：只在连接或服务恢复后重试一次；同一错误再次出现就停止并汇报。
- 不要从错误文本推断不存在的 fallback 路径、默认服务器或旧凭据。

遇到具体错误码时读取 [references/troubleshooting.md](references/troubleshooting.md)。

## 按需读取参考

- 配置、认证、case 生命周期、文件导入或显式导出、输出模式：读取 [references/common-operations.md](references/common-operations.md)。
- 从历史 task 恢复输入并创建多个修改方案：读取 [references/historical-case-branching.md](references/historical-case-branching.md)。
- 设备表分类、sheet/row CRUD、字段模板、储能关系：读取 [references/device-tables.md](references/device-tables.md)。
- 选择或校验时序 `type`、区分标准键与前端展示名、确认 `value_type`：建模前必须读取 [references/timeseries-types.md](references/timeseries-types.md)。
- 本地模板、XLSX 导入、通用绑定、TMY、负荷预测、极端曲线、机组指定出力率：读取 [references/timeseries.md](references/timeseries.md)。
- 年内/跨年机组检修计划等算法预处理：读取 [references/algorithm-preprocessing.md](references/algorithm-preprocessing.md)。
- 参数读取、单点更新、patch/replace、常用路径：读取 [references/parameters.md](references/parameters.md)。
- 新建或修改模型、选择风光荷地区、把地市映射到负荷省份、设置时序和运行场景：必须读取 [references/modeling-guide.md](references/modeling-guide.md)。
- 选择计算模式、解释 `job_type`、判断 `.tc/.tr/BPA` 启动路径或区分历史类型：必须读取 [references/computation-modes.md](references/computation-modes.md)。
- 任务启动/监控、结果 group、按用户要求导出文件、日志诊断：读取 [references/tasks-results-logs.md](references/tasks-results-logs.md)。
- 最新结果、任意时刻电力平衡、工作位置图、设备曲线和新能源/负荷/储能/机组极值快速查询：读取 [references/result-query-recipes.md](references/result-query-recipes.md)。
- 市场模拟（job type 301）的节点电价、机组经济性、分电压等级统计及与滚动结果的区别：读取 [references/market-results.md](references/market-results.md)。
- 从建模到结果的完整顺序和校验策略：读取 [references/workflows.md](references/workflows.md)。
- 生成或分析模板 A Word 报告：读取 [references/analysis-guide.md](references/analysis-guide.md)。
- 判断某个 teap3 能力是否已由 CLI 覆盖，或理解有意排除的管理接口：读取 [references/core-compatibility.md](references/core-compatibility.md)。

只读取当前任务需要的参考文件，不要一次加载全部文档。

## 高频护栏

- 单任务状态：`teap -o json task status --task-record-id <id>`。
- 最新完成结果：`teap -o json task status --finished-only --finished-page -1 --finished-page-size 1`。
- 取消任务：先查状态，再运行 `teap -o json task cancel <id> --confirm`；只接受等待中或计算中。
- 删除 case：先用 `case list` 取得精确路径，再运行 `teap -o json case delete <case-path> --confirm`。
- 删除 result：先确认任务完成且有 `result_path`，再运行 `teap -o json result delete <id> --confirm`。
- 等待任务：`teap -o json task wait <id>`。
- 分析失败：`teap -o json log analyze --task-id <id>`。
- 发现结果：`teap -o json result groups <id-or-result-path>`；根据实际返回的 key、curve、`curve_units` 和 zone 再查询。
- 任意时刻平衡：先运行 `teap -o json result balance-hour <id-or-result-path> <zero-based-index>` 发现 `available_units`，再按需增加 `--unit <exact-unit>`。
- 工作位置图：`teap -o json result work-position <id-or-result-path>`，分区查询使用 `work_position_groups` 返回的精确 zone 后缀；发现 `_result.scenario_balance_df...` 时增加 `--scenario`；统计前用 `--unit <exact-unit>` 明确量纲。
- 设备曲线：先从 `result groups` 的 `curve_units` 选择单位，再加 `--metadata-only` 获取名称/索引，最后用 `--device-index <index> --unit <exact-unit>` 服务端过滤时序并在 CLI 侧换算。
- 时序类型：`teap -o json timeseries types <case-path> --sheet <device-sheet>` 返回当前服务支持的限定 `value`、展示 `label` 和 `recommended_value_type`；`p_rate` 推荐 `multiply`，其他类型推荐 `replace`。
- 读取精确结果：`teap -o json result get <id-or-result-path> -g <exact-group> [--unit <exact-unit>]`；不带 `--unit` 时数据未换算，`available_units` 给出 Core 返回的单位系数。
- 市场结果先发现：`teap -o json result market groups <id-or-result-path>`。
- 导出结果文件：仅在用户明确要求本地 `.tr` 时运行 `teap -o json result download-tr <id-or-result-path> -o <path>`；普通结果分析不得执行。
- 不要对 `.tr` 使用 `cat`、`head`、`tail`、无界 `strings` 或无界 `grep`。
- 不要把 `-t` 从一个子命令类推到另一个子命令；先运行对应的 `-h`。

## 完成汇报

只汇报实际完成的动作。执行任务后给出服务地址、case path、job type、task ID、最终状态和 result path；修改算例后给出触达的 sheet/row/parameter；生成报告后给出 `.docx` 路径和使用的 result group。没有执行或没有生成文件时明确说明。
