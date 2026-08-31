# CLI 失败处理

## 目录

- 统一错误契约
- 错误码处置
- 业务错误定位
- 凭据脱敏
- 停止条件

## 统一错误契约

JSON 失败写入 stderr：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_usage",
    "message": "Missing argument 'FILE_NAME'.",
    "retryable": false,
    "hints": ["Run `teap case row create --help` ..."],
    "api_code": null,
    "http_status": null
  }
}
```

Agent 必须先读取 `retryable`，再决定是否重试。`hints` 是下一步诊断，不是说明性文本。

## 错误码处置

| code | 可重试 | 处置 |
| --- | --- | --- |
| `invalid_usage` | 否 | 运行提示中的当前子命令 `--help`，修正参数 |
| `auth_required` | 否 | 运行 `auth status`；由宿主注入凭据或受控终端登录 |
| `service_unreachable` | 是 | 运行 `config show`，确认网络恢复后只重试一次 |
| `server_http_error` | 仅 408/429/5xx | 按 `http_status` 处理；服务恢复后只重试一次 |
| `api_error` | 否 | 保留服务端业务 message/api_code，执行定向 hint |
| `task_failed` | 否 | `log analyze --task-id`，先修 case/参数；随后可原地重启或按需创建独立新任务 |
| `task_not_found` | 否 | `task status` 选择真实 ID |
| `task_start_option_conflict` / `task_job_type_required` | 否 | task ID 形式移除新任务选项；路径形式补充明确的 `--job-type` |
| `task_start_source_conflict` / `duplicate_task_id` | 否 | 一个批次只传唯一正整数 ID；路径必须单独启动并指定 `--job-type` |
| `task_stopping` / `task_start_not_allowed` | 否 | 停止中只查询状态；完成任务转 result；只原地启动未开始或已修复的失败任务 |
| `task_start_not_applied` | 否 | 停止重试，回读状态并检查 task log 或服务端调度器 |
| `confirmation_required` | 否 | 先只读核对目标；只有用户明确授权破坏性操作时才按 hint 添加 `--confirm` |
| `task_not_cancellable` | 否 | 只选择等待中/计算中任务；停止中只监控，完成后转 result |
| `task_cancel_not_applied` | 否 | 停止重试并逐个查询任务；并发状态变化可能令后端取消请求成为 no-op |
| `case_not_found` / `invalid_case_path` | 否 | `case list --filter <exact-name>` 获取同一服务返回的精确 `.tc/.tg` path |
| `case_list_filter_required` | 否 | 添加精确 `--filter`；仅在用户明确接受全服务扫描时使用 `--allow-unfiltered` |
| `case_duplicate_operation_key_required` / `case_duplicate_operation_key_conflict` | 否 | 每个预期副本使用唯一稳定 key；同一副本重试必须复用原 key |
| `case_duplicate_name_invalid` | 否 | `--name` 必填且只能是纯文件名（无路径分隔符）；`.tc` 后缀可省略 |
| `case_duplicate_name_occupied` | 否 | 目标名已存在于服务端算例目录；按 hint `case list --filter <name>` 核对后换 `--name` 重跑，原 key 可复用 |
| `case_duplicate_outcome_unknown` | 否 | 停止所有等价 duplicate；按 hint 只读核对 `case list --filter <name>`，不能换 key 绕过 |
| `case_in_use` | 否 | 先取消关联 task 并等待状态稳定，再删除 case |
| `result_not_deletable` | 否 | 只选择已完成且有 `result_path` 的 task ID |
| `case_delete_not_applied` / `result_delete_not_applied` | 否 | 停止重试，核对所有权/权限与服务端日志 |
| `invalid_server_response` | 否 | 停止重试，报告 CLI/服务版本和响应契约不匹配 |
| `cli_error` | 否 | 按 hint 定位本地校验或响应缺失 |
| `unexpected_error` | 否 | 停止并报告最小复现，不猜 fallback |

文本输出也包含 `Error [code]`、`Retryable` 和一个或多个 `Hint`，含义相同。

## 业务错误定位

Sheet 不存在：

```bash
teap case structure "$CASE_PATH"
```

Row/index 不存在：

```bash
teap case sheet get "$CASE_PATH" <sheet>
```

参数 path 不存在：读取完整 parameter，确认任务分支和字段名。不要静默改用其他分支或旧键。

Task/result 不存在：

```bash
teap task status
```

结果 group 无法解析：先用 `result groups`、`parameter`、`_result.key_summaries` 复核。不要直接解析原始 `.tr`。

任务计算失败：

```bash
teap log analyze --task-id <id>
```

根据 finding 定点读取设备、时序或参数。修正所有目标所引用的输入后，可执行 `teap task start <id> [<id> ...]` 一次原地重启并保留 ID；只有明确需要独立执行记录时，才从 case/result 服务端路径创建新 task。不得把未修正的失败任务加入批次，也不得在 `task_start_not_applied` 后原样重试整批。

## 凭据脱敏

CLI 会对结构化 token 字段、Bearer 文本和常见 token 参数做统一脱敏。仍应遵守：

- 不读取凭据文件；
- 不把 token 放在参数、环境转储、截图、日志或异常复现中；
- 测试只使用假 token，并断言 stdout/stderr 不含它；
- 错误 message 中若出现未脱敏敏感值，停止传播原文并报告安全缺陷。

不要为了诊断认证失败打印请求头或完整配置。

## 停止条件

满足任一条件就停止原样重试：

- `retryable=false`；
- 可重试错误已在恢复后重试一次仍失败；
- `invalid_server_response` 或 `unexpected_error`；
- task 已进入失败终态；
- hint 要求用户/宿主提供凭据、权限或服务恢复；
- 下一步会改变业务模型，但当前错误没有足够证据支持该修改。

停止后汇报：失败命令类别、错误 code、已执行的只读诊断、关键 message、下一步所需条件。不得汇报 token 或原始大日志。
