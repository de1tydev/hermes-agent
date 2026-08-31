# 算例生命周期

## 目录

- 创建算例
- 模板、样例、复制与组合
- 导入与显式导出
- 删除算例
- Sheet XLSX 交换
- 结构与校验
- 参数自动补全

负责对某个具体 `case/.tc` 的创建、修改、删除与文件交换。发现/搜索算例见 [general-operation/environment-and-discovery.md](../general-operation/environment-and-discovery.md)。

## 创建算例

新建服务端空算例：

```bash
teap case create demo
```

记录返回的 `path`，后续所有写操作使用服务端 path，不要用展示 URL 代替文件名。常用创建选项以 `teap case create -h` 为准。不要猜模板名；使用 `case templates` 查询服务端公开模板。

## 模板、样例、复制与组合

```bash
teap case templates
teap case examples list
```

普通建模直接使用服务端模板、样例和 case 能力。只有用户明确要求把样例保存到本地时，才运行 `teap case examples download <example-name> -o ./example.tc`。

`case duplicate` 是服务端文件复制：从 task ID（自动取其 `.tc` 输入或完成 `.tr` 的内嵌输入）或服务端 `.tc/.tr` 复制出一个新命名的可编辑 `.tc`，返回算例存放目录中的 `path` 并出现在 `case list` 中；不创建 task、不经过 XLSX/import、不下载。

```bash
teap case duplicate --task-record-id 12345 --name plan-a --operation-key task-12345-plan-a --confirm
teap case duplicate --file-path /server/path/source.tc --name plan-b --operation-key source-plan-b --confirm
```

`--name` 必填且只能是纯文件名；重名返回 `case_duplicate_name_occupied`（`retryable=false`），换名重跑即可（原 key 可复用）。每个副本一个唯一 operation key；同一副本重试复用原 key，成功后重放返回缓存响应；`case_duplicate_outcome_unknown` 时停止并按 hint 只读核对，不得换 key 绕过护栏。

从历史任务创建多个修改方案时读取 [application-instructions/historical-branching.md](../application-instructions/historical-branching.md)。

服务端已有算例必须直接传服务端 path；只有用户提供了本地 `.tc` 时才使用本地来源。同一次调用不能混用两类来源：

```bash
teap case merge ./a.tc ./b.tc --name merged
teap case merge /server/a.tc /server/b.tc --name merged
```

按分区或母线拆分服务端 case：

```bash
teap case split /server/merged.tc --by zone --row-id 1 --row-id 3
```

以后端返回的 path/ID 为准，不从输入名猜输出位置。

## 导入与显式导出

`case import` 同时接受本地文件和服务端 path。只有参数对应本地现存文件时才 multipart 上传；服务端 path 使用 `file_path` 表单直接转换，不产生下载或重新上传：

```bash
teap case import ./demo.tc
teap case import /server/input/source.xlsx
```

服务端已有源文件时必须使用第二种形式。记录返回的 `.tc` `path`，后续直接编辑或服务端分叉。

以下下载命令只在用户明确要求取得、导出、交付、保存或本地检查文件时使用。普通算例修改、复制和方案分叉均继续操作服务端 path，不得先下载再上传。

用户明确要求导出某个历史 task 的输入 `.tc` 时：

```bash
teap case download 12345 -o ./task-12345.tc
```

用户明确要求导出当前服务端 case 或其他格式时：

```bash
teap file download --tc /server/path/demo.tc --type tc -o ./demo.tc
teap file download --tc /server/path/demo.tc --type xlsx -o ./demo.xlsx
```

用户明确要求导出结果文件时，`.tr` 和结果内嵌 `.tc` 分别使用 `result download-tr/download-tc`。`result download-tc` 只生成本地文件，不是继续修改、复制或分叉算例的入口；复制与分叉直接使用 `case duplicate`（其产物已在服务端算例目录，无需再 import）。详见 [teapresult/result-lifecycle.md](../teapresult/result-lifecycle.md)。

## 删除算例

删除的是可编辑输入文件，不是 task 或 result。先从同一服务查询精确 path：

```bash
teap case list --filter demo
teap case delete /server/path/demo.tc --confirm
```

只接受 `case list` 对当前用户可见的服务端 `.tc/.tg` path。命令会拒绝仍被等待中、计算中或停止中任务引用的 case；先使用 `task cancel` 并等待任务状态稳定。删除 case 不会删除既有完成结果，需要删除结果时使用 [teapresult/result-lifecycle.md](../teapresult/result-lifecycle.md)。

`case_not_found`、`invalid_case_path`、`case_in_use` 和 `case_delete_not_applied` 均不可原样重试。按 hint 执行只读检查；不要猜路径、切换服务或自动添加 `--confirm`。

## Sheet XLSX 交换

```bash
teap case sheet export-xlsx "$CASE_PATH" load -o ./load.xlsx
teap case sheet import-xlsx "$CASE_PATH" load ./load.xlsx
```

这是单个设备 sheet 的交换接口，不等同于时序 XLSX 导入。时序文件必须使用 `timeseries file ...`，见 [teapcase/timeseries-write.md](timeseries-write.md)。导入后定点回读目标 sheet。

## 结构与校验

不确定 sheet 名、字段或导入算例结构时：

```bash
teap case structure /server/path/demo.tc
```

不要在每个新建空算例开头读取全部结构。常用设备可直接用字段模板创建；只有非常见表、导入结构或服务端拒绝字段时再查结构。

提交任务前按 [SKILL.md 的校验节奏](../../SKILL.md#校验节奏) 执行唯一的提交门禁。当前 CLI 会检查自身明确掌握的跨字段约束；TEAP 服务仍会执行完整业务校验。CLI 校验通过不代表可以跳过任务失败诊断。

## 参数自动补全

仅在用户明确授权修改后运行 `python skills/teap-cli-ops/scripts/autofill_case_parameters.py <case-path> --confirm`。该脚本委托 `case autofill` 调用 TEAP Core 的参数补充功能；不得自行猜默认值，也不得自动追加 `--confirm`。同一 `.tc` 的写操作必须串行，row index 分配依赖当前文件状态。
