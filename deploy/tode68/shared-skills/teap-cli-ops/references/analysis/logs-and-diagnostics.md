# 日志诊断与异常定位

## 目录

- 对象边界
- 任务失败诊断
- 日志定点工具
- 离线诊断纪律
- 完成汇报

配合 [analysis/troubleshooting.md](troubleshooting.md) 的错误契约一起使用：错误码处置看 troubleshooting，定位具体失败根因看本文。

## 对象边界

`log` 是 TEAP 管理端日志；任务失败先读任务结构化的 `failure` 摘要，再按需使用 `log analyze`。日志属于诊断信息，不修改 case/task/result。

## 任务失败诊断

任务失败的默认入口：

```bash
teap log analyze --task-id 12345
```

该命令优先使用用户级任务日志。只有明确需要且具备权限时才加全局日志：

```bash
teap log analyze --task-id 12345 \
  --include-global --logs error.log,solver.log
```

任务等待失败时先读结构化 `failure.findings` 和 `failure.next_commands`。`task_failed` 不可原样重试；按 `failure.findings` 修正 case、参数或日志中定位的问题，不得反复启动同一 case。

## 日志定点工具

```bash
teap log list
teap log tail error.log --lines 200
teap log search infeasible --logs error.log,solver.log \
  --max-matches 50 --context 3
```

诊断顺序：状态 -> failure 摘要 -> task log analyze -> 必要的全局片段 -> 回读相关 sheet/parameter。不要先下载完整日志，也不要在没有证据时大范围改 case。

## 离线诊断纪律

禁止把 `.tr` 原始内容输出到模型上下文。不得使用 `cat/head/tail`、无界 `strings/grep`。确需离线诊断时，用受控解析器读取明确 key，并限制输出行数与字节数。

结构化接口没有目标数据或无法解析结果时，报告已尝试的 group/curve 和具体限制；只有用户明确要求取得本地文件后才下载 `.tr`，不得自动转为本地解析。离线解析使用 `skills/teap-cli-ops/scripts/` 中的受控脚本，限定 key 和输出大小。

## 完成汇报

至少给出：服务地址、case 名/path、job type、task ID、状态、result path、检查过的 result group。失败时给出日志 finding 和下一步，不要声称已有结果。生成报告时再给出 `.docx` 路径。
