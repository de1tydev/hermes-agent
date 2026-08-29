# 通用操作与算例生命周期

## 目录

- 全局选项
- 服务与认证
- 算例查找和创建
- 模板、样例、复制与组合
- 导入与显式导出
- 删除算例
- Sheet XLSX 交换
- 结构与校验
- 输出与审计

## 全局选项

全局选项必须放在命令组之前：

```bash
teap -o json --base-url https://teap.example case list
```

常用选项：

| 选项 | 含义 |
| --- | --- |
| `-o, --output table|json|raw-json` | 输出模式；Agent 默认用 `json` |
| `-u, --base-url <url>` | 仅覆盖本次调用的 TEAP 地址 |
| `-T, --timeout <seconds>` | HTTP 超时，不是任务等待超时 |
| `--verify-ssl / --insecure` | TLS 校验开关 |
| `-h, --help` | 当前层级帮助 |

不要把结果下载命令自身的 `-o <file>` 和全局 `-o json` 混淆；需要两者时把全局选项放在最前。

## 服务与认证

查看有效配置，输出会统一脱敏：

```bash
teap -o json config show
```

持久化通用服务地址：

```bash
teap -o json config set-server https://teap.example
```

检查目标服务是否要求认证：

```bash
teap -o json auth status
```

不要从环境变量或凭据文件存在与否推断服务认证要求。`auth status` 会匿名探测，再在有凭据时校验当前 token，且不会输出 token。

`auth login/token/refresh/logout` 只在用户明确要求管理本地认证且当前环境允许时使用。Agent 不应把 token 写进命令、临时文件、日志或回复。

## 算例查找和创建

按名称过滤：

```bash
teap -o json case list -f ieee
```

不要用 `case list` 回答“最新计算完成”“最新结果”；那是 task/result 查询。

新建服务端空算例：

```bash
teap -o json case create demo
```

记录返回的 `path`，后续所有写操作使用服务端 path，不要用展示 URL 代替文件名。

常用创建选项以 `teap case create -h` 为准。不要猜模板名；使用 `case templates` 查询服务端公开模板。

## 模板、样例、复制与组合

```bash
teap -o json case templates
teap -o json case examples list
```

普通建模直接使用服务端模板、样例和 case 能力。只有用户明确要求把样例保存到本地时，才运行 `teap -o json case examples download <example-name> -o ./example.tc`。

`case duplicate` 复制历史 task 输入或任务关联的服务端 case。当前 Core `develop` 通常返回服务端 XLSX，而不是可直接编辑的 `.tc`；按响应中的 `server_input_path`、`editable` 和 `next_action` 继续，不要下载后重新上传。

```bash
teap -o json case duplicate --task-record-id 12345
teap -o json case duplicate --file-path /server/path/source.tc
```

从历史任务创建多个修改方案时读取 [historical-case-branching.md](historical-case-branching.md)。

服务端已有算例必须直接传服务端 path；只有用户提供了本地 `.tc` 时才使用本地来源。同一次调用不能混用两类来源：

```bash
teap -o json case merge ./a.tc ./b.tc --name merged
teap -o json case merge /server/a.tc /server/b.tc --name merged
```

按分区或母线拆分服务端 case：

```bash
teap -o json case split /server/merged.tc --by zone --row-id 1 --row-id 3
```

以后端返回的 path/ID 为准，不从输入名猜输出位置。

## 导入与显式导出

`case import` 同时接受本地文件和服务端 path。只有参数对应本地现存文件时才 multipart 上传；服务端 path 使用 `file_path` 表单直接转换，不产生下载或重新上传：

```bash
teap -o json case import ./demo.tc
teap -o json case import /server/input/source.xlsx
```

服务端已有源文件时必须使用第二种形式。记录返回的 `.tc` `path`，后续直接编辑或服务端分叉。

以下下载命令只在用户明确要求取得、导出、交付、保存或本地检查文件时使用。普通算例修改、复制和方案分叉均继续操作服务端 path，不得先下载再上传。

用户明确要求导出某个历史 task 的输入 `.tc` 时：

```bash
teap -o json case download 12345 -o ./task-12345.tc
```

用户明确要求导出当前服务端 case 或其他格式时：

```bash
teap -o json file download --tc /server/path/demo.tc --type tc -o ./demo.tc
teap -o json file download --tc /server/path/demo.tc --type xlsx -o ./demo.xlsx
```

用户明确要求导出结果文件时，`.tr` 和结果内嵌 `.tc` 分别使用 `result download-tr/download-tc`。`result download-tc` 只生成本地文件，不是继续修改、复制或分叉算例的入口；这些操作使用 `case duplicate` 和服务端 `case import`。详见 [tasks-results-logs.md](tasks-results-logs.md)。

## 删除算例

删除的是可编辑输入文件，不是 task 或 result。先从同一服务查询精确 path：

```bash
teap -o json case list --filter demo
teap -o json case delete /server/path/demo.tc --confirm
```

只接受 `case list` 对当前用户可见的服务端 `.tc/.tg` path。命令会拒绝仍被等待中、计算中或停止中任务引用的 case；先使用 `task cancel` 并等待任务状态稳定。删除 case 不会删除既有完成结果，需要删除结果时使用 `result delete`。

`case_not_found`、`invalid_case_path`、`case_in_use` 和 `case_delete_not_applied` 均不可原样重试。按 hint 执行只读检查；不要猜路径、切换服务或自动添加 `--confirm`。

## Sheet XLSX 交换

```bash
teap -o json case sheet export-xlsx "$CASE_PATH" load -o ./load.xlsx
teap -o json case sheet import-xlsx "$CASE_PATH" load ./load.xlsx
```

这是单个设备 sheet 的交换接口，不等同于时序 XLSX 导入。时序文件必须使用 `timeseries file ...`，见 [timeseries.md](timeseries.md)。导入后定点回读目标 sheet。

## 结构与校验

不确定 sheet 名、字段或导入算例结构时：

```bash
teap -o json case structure /server/path/demo.tc
```

不要在每个新建空算例开头读取全部结构。常用设备可直接用字段模板创建；只有非常见表、导入结构或服务端拒绝字段时再查结构。

提交任务前校验：

```bash
teap -o json case validate /server/path/demo.tc
```

当前 CLI 会检查自身明确掌握的跨字段约束；TEAP 服务仍会执行完整业务校验。CLI 校验通过不代表可以跳过任务失败诊断。

## 输出与审计

`json` 是经过紧凑化和脱敏的稳定 Agent 输出；`raw-json` 保留后端载荷但仍经过统一脱敏。不要把 raw-json 作为默认模式。

成功写操作后记录：

- case path；
- operation；
- sheet 和 row ID；
- 自动补全或 companion 关系；
- 时序 ID 与 bindings；
- task ID 和 result path。

失败时读取 `error.code`、`error.retryable`、`error.hints`。不可重试错误不得原样重放。
