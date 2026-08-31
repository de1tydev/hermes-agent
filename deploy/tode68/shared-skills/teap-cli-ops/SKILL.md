---
name: teap-cli-ops
description: 通过 `teap` CLI 操作 TEAP 电力系统规划与仿真平台。用于解释 TEAP，管理或删除 case/.tc，编辑设备表、参数与时序，执行预处理算法，启动、监控或取消 task，诊断失败日志，查询、导入、按用户明确要求导出或删除 result/.tr，以及按模板生成 Word 仿真分析报告。
---

# TEAP CLI Ops

优先使用已安装的 `teap` 命令完成 TEAP3 算例建模、任务执行和结果分析。不要用 `curl`、临时 Python HTTP 请求或宿主专用路径绕开 CLI。

默认只操作服务端对象：算例建模、修改、复制、方案分叉、任务执行和结果分析均通过 `teap` 的服务端命令完成，不下载 `.tc/.tr`。只有用户明确要求取得、导出、交付、保存或在本地检查文件时，才执行任何 case/result/file download；结构化接口不足时应说明限制并询问用户是否需要本地文件，不得自动下载解析。

默认输出是 `json`（稳定、紧凑、可机器判定的 Agent 契约），命令不需要显式 `-o json`；只在人工浏览时使用 `-o table`，只在调试后端原始契约时使用 `-o raw-json`。

## 先路由对象

- 用户说“算例、模型、`.tc`、设备、参数、时序”时，按可编辑 `case` 处理。
- 用户说“运行、计算、任务、进度、失败”时，按一次执行记录 `task` 处理。
- `task start <task-id> [<task-id> ...]` 一次原地启动/重启一个或多个未开始或已修复的失败任务，保留 ID 和原提交参数；`task start <case-or-result-path> --job-type <id>` 才创建新 task。不要为原地启动下载或复制 `.tc`。
- `case start <task-id> [<task-id> ...]` 是同一批量启动能力的薄别名；多个 case path 不伪装成原子批量提交。
- 用户说“结果、报告、`.tr`、最新完成、计算完成的算例”时，按已完成 `task/result` 处理；不要用 `case list` 代替结果查询。
- 用户说“最新完成”时，只请求已完成任务的最后一页且每页一条：`teap task status --finished-only --finished-page -1 --finished-page-size 1`；不要拉取完整任务列表。
- 用户按名称找结果时使用 `teap result search --filter <name>`；`case list` 只查可编辑输入，并且必须带 `--filter`。只有用户明确接受全服务扫描时才加 `--allow-unfiltered`。
- 用户说“取消、暂停、停止正在计算”时按 `task cancel` 处理；“删除输入模型”按 `case delete`；“删除已完成结果”按 `result delete`。三者不可互换。

需要平台和对象背景时读取 [references/general-operation/platform-overview.md](references/general-operation/platform-overview.md)。

## 场景与时序判定（解读侧）

任务和结果绑定计算场景（`case_info.scenario_selected`，在 `task status --task-record-id <id>` 与 result inventory 中可确认）。case 内的时序曲线按 `timeseries.scenario` 组织：同一设备曲线可以有多个场景版本，一次计算只使用所选场景的版本。

- 某设备在当前场景下没有曲线版本时，该次计算将其时序视为“未指定/按 0 计算”，result 的 `sim_log` 会给出警告（如“以下【电力流】的【送端、受端利用小时区间】或【时序曲线】都没有指定”）。这是所选场景下缺曲线，不代表 case 文件整体缺曲线。
- 解读 result 或 sim_log 的“缺曲线/按 0”告警：先锚定该任务的场景，再按场景过滤该 case 的 `timeseries` 行判断是否缺失；不要拿其他场景下的非空曲线判定历史任务。`case sheet get <case> timeseries --row-id <id>` 返回的曲线行带 `scenario` 字段，为空表示无场景限定（通用）。
- 为某场景补曲线时，优先复用 `scenario` 为空（通用）或与目标场景同名的现有曲线；写入后必须回读 `case_info.scenario_selected` 与曲线行，确认场景一致（详见 [references/teapcase/parameters.md](references/teapcase/parameters.md) 与 [references/teapcase/timeseries-write.md](references/teapcase/timeseries-write.md)）。

## 时序类型与数据读取

时序类型的权威来源是 TEAP Core 的 `teap/config/tc_structure.yml`（各设备表属性中 `change_with_timeseries: true` 或 `extra_ts_type` 定义的字段），经服务端口径下发，CLI 用 `teap timeseries types <case-path> --sheet <device-sheet>` 读取；规范形式为 `<sheet>.<type_key>` 限定值（如 `feedin.p_rate`）。不要用前端 label、中文展示名或任意字符串作为类型。

- 写任何 timeseries（`case row create/update`、`case sheet write`）都必须带限定且合法的 type；不属于当前 case 服务配置的类型会被 CLI 拒绝。相关稳定错误码：`timeseries_type_required`、`timeseries_type_sheet_mismatch`、`timeseries_type_unqualified`、`invalid_timeseries_type`、`timeseries_type_sheet_unsupported`、`invalid_timeseries_value_type`。失败 hint 会指向 `teap timeseries types <case-path> --sheet <sheet>` 给出的合法值；不要原样重试。
- 写入时的编辑器契约：timeseries 行的 type 以裸键（如 `p_rate`）下发，服务端按 `ref_element` 组装限定值；CLI 已自动处理，不要手动改。
- 读取数值：结构化接口（`case sheet get timeseries` / `case row get`）返回每条曲线的摘要（`type/value_type/scenario/ref_element/max_value/min_value/sum_value/relation_count`），服务端不回传完整 value 数组。判断“曲线是否绑定/非空”用摘要即可；确需完整曲线数据时，用 `case sheet export-xlsx <case-path> timeseries -o <本地文件>`（服务端导出）交给代码处理，不要把 8760 级数组直接灌入对话。所有读取都在服务端完成，不使用下载的 .tc/.tr 做本地解析。

## 执行纪律

1. 每次 skill 首次触发，先运行 `python skills/teap-cli-ops/scripts/check_teap_environment.py`。只按脚本的 `ready`、服务可达性和认证状态继续；不得读取或返回 token。认证过期时停止业务命令，让宿主刷新页面或会话以更新凭据。
2. 查找已有算例，或创建/导入算例；记录服务端 case path。
3. 按依赖顺序串行编辑同一个 `.tc`：分区 -> 母线 -> 设备 -> 时序 -> 参数。创建或修改时序前必须运行 `teap timeseries types <case-path> --sheet <device-sheet>`，只使用返回的 `types[].value` 和 `replace|multiply`；不得猜测设备表、类型键或数据合并方式。
4. 修改已有对象前读取相关 sheet/row；新建空算例可使用模板字段直接创建，再做一次定点校验。Core 字段名是 `value_type`；CLI 接受 `data_type` 作为输入别名，但不得同时给出冲突值。成功输出含 `timeseries_value_type_not_recommended` warning 时，先核对业务语义再决定是否保留该写入。
5. 一个连续建模批次完成全部预期写入、参数场景选择和定点回读后，在启动任务前运行一次 `python skills/teap-cli-ops/scripts/validate_case_for_submission.py <case-path>` 作为提交门禁。修正全部 `issues`，逐项确认 `warnings` 后才能提交；预检后如果 case 再发生写入，必须在启动前重新预检；没有 case 变化不得重复运行。不得用旧的窄范围 `case validate` 代替完整预检。
6. 任务失败时先逐项读取 `failure` 并运行 `log analyze --task-id <id>`；全部目标修正后可用一个 `task start <id> [<id> ...]` 批量原地重启，未修正时不得加入批次。对服务端 case 创建新 task 时，先按第 5 条完成一次提交预检，再运行 `teap task start <case-path> --job-type <id>`；启动前把 case path、job type、note 和 job 配置展示给用户，用户未要求执行时不擅自启动耗时任务。
7. 结果查询必须按“最小分页 -> 动态发现 -> 精确 group/设备曲线与单位 -> 有界本地聚合”执行。先运行 `teap result groups <id-or-result-path> --summary-only`；设备曲线从 `curve_units` 选择精确单位，平衡表先查询一次并从 `available_units` 选择单位，再用 `--unit <exact-unit>` 取得已换算 JSON。需要全年数组时再去掉 `--summary-only`，不要因为猜测的 group 或单位失败就下载 `.tr`。
8. 结构化接口没有目标数据或无法解析结果时，报告已尝试的 group/curve 和具体限制；只有用户明确要求取得本地文件后才下载 `.tr`，不得自动转为本地解析。
9. 取消或删除前先读取目标状态，并且只有用户明确要求该破坏性操作时才添加 `--confirm`；不得把确认选项作为失败后的自动重试手段。
10. 复制算例就是服务端文件复制：`teap case duplicate --task-record-id <id>|--file-path <server .tc/.tr> --name <new-name> --operation-key <key> --confirm`，返回算例存放目录中新的可编辑 `.tc` `path`。`--name` 必填且不得与已有算例重名（重名返回 `case_duplicate_name_occupied`，换名重跑即可）；每个副本一个唯一 key，前台串行执行，同一副本重试复用原 key；`case_duplicate_outcome_unknown` 时停止并按 hint 只读核对。不得下载、本地 cp 或重新上传。

同一个 `.tc` 的写操作必须串行，row index 分配依赖当前文件状态。不要为了“确认”重复成功的写命令。

## 校验节奏

`validate_case_for_submission.py` 是提交门禁，不是每次写入后的回读命令：

- row、时序或参数写入后，只回读受影响的 row、曲线或参数；不要在每个写操作后运行完整预检。
- 同一个 case 的连续修改只在本批次结束、场景参数回读完成后运行一次完整预检。
- 预检发现 `issues` 时，修复后重新回读变更对象并再次预检；如果修复过程中又有写入，也必须重新预检。
- 同一个未改变的 case 不因进入多个参考章节、重复解释或准备同一 task 而重复预检。
- `case validate` 仅用于需要窄范围储能字段检查的兼容场景；它不能替代提交门禁，也不应作为完整预检的自动重试入口。

## 固化脚本

- 环境自检：`python skills/teap-cli-ops/scripts/check_teap_environment.py`。输出仅包含 CLI 版本、服务 URL、可达性和认证布尔状态，不输出 token、认证文件或配置路径。
- 提交前预检：`python skills/teap-cli-ops/scripts/validate_case_for_submission.py <case-path>`。动态读取当前 case 的 Core 字段元数据与 `ts_type` 清单，检查在运设备必填字段、时序类型/数据类型、`stogen` 到 `storage` 绑定以及所选场景的时序覆盖。
- 整算例参数补全：仅在用户明确授权修改后运行 `python skills/teap-cli-ops/scripts/autofill_case_parameters.py <case-path> --confirm`。该脚本委托 `case autofill` 调用 TEAP Core 的参数补充功能；不得自行猜默认值，也不得自动追加 `--confirm`。
- 结果摘要：`teap result groups <task-id-or-result-path> --summary-only` 和 `teap result work-position <task-id-or-result-path> --summary-only` 只返回有界 inventory 或每条曲线的 `count/min/max/mean`；不要将摘要模式与 `--output raw-json` 混用。

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

遇到具体错误码时读取 [references/analysis/troubleshooting.md](references/analysis/troubleshooting.md)。

## 按需读取参考

- 平台概览、对象边界、环境检查、认证、输出模式、算例/结果发现、计算模式、任务生命周期：读取 [references/general-operation/](references/general-operation/)（[platform-overview](references/general-operation/platform-overview.md)、[environment-and-discovery](references/general-operation/environment-and-discovery.md)、[computation-modes](references/general-operation/computation-modes.md)、[task-lifecycle](references/general-operation/task-lifecycle.md)）。
- 具体算例的创建/编辑/删除、设备表、参数、时序写入与类型：读取 [references/teapcase/](references/teapcase/)（[case-lifecycle](references/teapcase/case-lifecycle.md)、[device-tables](references/teapcase/device-tables.md)、[parameters](references/teapcase/parameters.md)、[timeseries-write](references/teapcase/timeseries-write.md)、[timeseries-types](references/teapcase/timeseries-types.md)）。
- 具体结果的读取、分析与导出：读取 [references/teapresult/](references/teapresult/)（[result-lifecycle](references/teapresult/result-lifecycle.md)、[query-recipes](references/teapresult/query-recipes.md)、[market-results](references/teapresult/market-results.md)）。
- 错误契约、错误码处置、日志诊断与平台异常定位：读取 [references/analysis/troubleshooting.md](references/analysis/troubleshooting.md) 与 [references/analysis/logs-and-diagnostics.md](references/analysis/logs-and-diagnostics.md)。
- TMY、负荷预测、风光极端曲线等数据生成与机组检修计划：读取 [references/application-function/data-generation.md](references/application-function/data-generation.md) 与 [references/application-function/maintenance-planning.md](references/application-function/maintenance-planning.md)。
- 从 0 新建算例、常见结果分析、比较两个算例、端到端工作流、历史分叉、模板 A 报告：读取 [references/application-instructions/](references/application-instructions/)（[build-case-from-scratch](references/application-instructions/build-case-from-scratch.md)、[common-result-analysis](references/application-instructions/common-result-analysis.md)、[compare-two-cases](references/application-instructions/compare-two-cases.md)、[end-to-end-workflow](references/application-instructions/end-to-end-workflow.md)、[historical-branching](references/application-instructions/historical-branching.md)、[report-generation](references/application-instructions/report-generation.md)）。

只读取当前任务需要的参考文件，不要一次加载全部文档。

## 高频护栏

- 单任务状态：`teap task status --task-record-id <id>`。
- 最新完成结果：`teap task status --finished-only --finished-page -1 --finished-page-size 1`。
- 按名称查结果：`teap result search --filter <name>`；不要改用无过滤 `case list`。
- 取消任务：先查状态，再运行 `teap task cancel <id> --confirm`；只接受等待中或计算中。
- 删除 case：先用 `case list --filter <exact-name>` 取得精确路径，再运行 `teap case delete <case-path> --confirm`。
- 删除 result：先确认任务完成且有 `result_path`，再运行 `teap result delete <id> --confirm`。
- 等待任务：`teap task wait <id>`。
- 分析失败：`teap log analyze --task-id <id>`。
- 发现结果：`teap result groups <id-or-result-path>`；根据实际返回的 key、curve、`curve_units` 和 zone 再查询。
- 任意时刻平衡：先运行 `teap result balance-hour <id-or-result-path> <zero-based-index>` 发现 `available_units`，再按需增加 `--unit <exact-unit>`。
- 工作位置图：`teap result work-position <id-or-result-path>`，分区查询使用 `work_position_groups` 返回的精确 zone 后缀；发现 `_result.scenario_balance_df...` 时增加 `--scenario`；统计前用 `--unit <exact-unit>` 明确量纲。
- 设备曲线：先从 `result groups` 的 `curve_units` 选择单位，再加 `--metadata-only` 获取名称/索引，最后用 `--device-index <index> --unit <exact-unit>` 服务端过滤时序并在 CLI 侧换算。
- 场景敏感告警：result/sim_log 的“缺曲线/按 0”告警按该任务的场景解读，先确认 `task status` 的场景，再看该场景在 case `timeseries.scenario` 的覆盖；`scenario` 为空表示通用（所有场景可用）。详见“场景与时序判定（解读侧）”。
- 大体量时序表分页：`case sheet get <case-path> timeseries --index-range <start>-<end>` 用服务端有界分页读取（每页建议 ≤4000 行），避免单次拉全表越限；`--row-id` 与 `--index-range` 互斥。
- 时序数据完整值：服务端结构读取只给摘要（max/min/sum）；需要数值时用 `case sheet export-xlsx <case-path> timeseries -o <file>` 落盘后由代码处理。
- 时序类型：`teap timeseries types <case-path> --sheet <device-sheet>` 返回当前服务支持的限定 `value`、展示 `label` 和 `recommended_value_type`；`p_rate` 推荐 `multiply`，其他类型推荐 `replace`。
- 读取精确结果：`teap result get <id-or-result-path> -g <exact-group> [--unit <exact-unit>]`；不带 `--unit` 时数据未换算，`available_units` 给出 Core 返回的单位系数。
- 市场结果先发现：`teap result market groups <id-or-result-path>`。
- 导出结果文件：仅在用户明确要求本地 `.tr` 时运行 `teap result download-tr <id-or-result-path> -o <path>`；普通结果分析不得执行。
- 不要对 `.tr` 使用 `cat`、`head`、`tail`、无界 `strings` 或无界 `grep`。
- 不要把 `-t` 从一个子命令类推到另一个子命令；先运行对应的 `-h`。

## 完成汇报

只汇报实际完成的动作。执行任务后给出服务地址、case path、job type、task ID、最终状态和 result path；修改算例后给出触达的 sheet/row/parameter；生成报告后给出 `.docx` 路径和使用的 result group。没有执行或没有生成文件时明确说明。
