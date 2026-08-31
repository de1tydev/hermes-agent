# 环境、配置与对象发现

## 目录

- 全局选项与输出模式
- 环境自检
- 服务与认证
- 发现算例
- 发现结果
- 输出与审计

## 全局选项与输出模式

全局选项必须放在命令组之前：

```bash
teap --base-url https://teap.example case list
```

常用选项：

| 选项 | 含义 |
| --- | --- |
| `-o, --output table\|json\|raw-json` | 输出模式；`json` 是默认值 |
| `-u, --base-url <url>` | 仅覆盖本次调用的 TEAP 地址 |
| `-T, --timeout <seconds>` | HTTP 超时，不是任务等待超时 |
| `--verify-ssl / --insecure` | TLS 校验开关 |
| `-h, --help` | 当前层级帮助 |

不要把结果下载命令自身的 `-o <file>` 和全局输出选项 `-o/--output` 混淆；需要两者时把全局选项放在最前。`json` 是默认输出，经过紧凑化和脱敏，是稳定的 Agent 契约；人工浏览时显式 `-o table`；调试后端原始契约时才用 `-o raw-json`（仍经过统一脱敏），不要把 raw-json 作为默认模式。

## 环境自检

每次 skill 首次触发，先运行环境自检脚本，按返回的 `ready`、服务可达性和认证状态继续；不要读取或返回 token：

```bash
python skills/teap-cli-ops/scripts/check_teap_environment.py
```

输出仅包含 CLI 版本、服务 URL、可达性和认证布尔状态，不输出 token、认证文件或配置路径。

## 服务与认证

查看有效配置，输出会统一脱敏：

```bash
teap config show
```

持久化通用服务地址：

```bash
teap config set-server https://teap.example
```

检查目标服务是否要求认证：

```bash
teap auth status
```

不要从环境变量或凭据文件存在与否推断服务认证要求。`auth status` 会匿名探测，再在有凭据时校验当前 token，且不会输出 token。`auth login/token/refresh/logout` 只在用户明确要求管理本地认证且当前环境允许时使用。Agent 不应把 token 写进命令、临时文件、日志或回复。

## 发现算例

按名称过滤：

```bash
teap case list -f ieee
```

`case list` 会在 Core 中扫描并排序服务端 case 后才分页，因此 CLI 默认拒绝无过滤调用。只有用户明确接受全服务扫描时才使用 `--allow-unfiltered`。不要用 `case list` 回答“最新计算完成”“最新结果”；那是 task/result 查询，应使用 `result search --filter <name>` 或 `task status --finished-only`。

删除 case 前先经过 `case list --filter <exact-name>` 取得精确服务端 path。

## 发现结果

“最新完成”“计算完成的算例”“结果、报告”必须从 task/result 路由，不要用 `case list` 代替。先查 `task status`，选择完成且有 `result_path` 的记录。

```bash
teap task status --finished-only --finished-page -1 --finished-page-size 1
teap result search --filter 华东 --page-size 20
```

`--finished-page -1` 使用后端最后一页语义。找“最新完成”时将完成页大小限制为 1，不要拉取完整历史。

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
