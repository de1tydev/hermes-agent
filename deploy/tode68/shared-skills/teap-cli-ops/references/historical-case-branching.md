# 从历史任务创建修改方案

只走服务端链路：

```text
task ID -> case duplicate -> server_input_path -> case import -> editable .tc
editable .tc -> case duplicate + case import -> independent plan .tc
```

`case duplicate` 当前通常返回服务端 XLSX。执行响应中的 `next_action.command`，不要把它当作可编辑 `.tc`。`case import <server-path>` 使用服务端 `file_path` 转换，不会下载或上传。

创建三个方案：

```bash
BASE_DUP=$(teap -o json case duplicate --task-record-id 4884)
BASE_TC=$(teap -o json case import "$(printf '%s' "$BASE_DUP" | jq -er '.server_input_path')" | jq -er '.path')

P0_DUP=$(teap -o json case duplicate --file-path "$BASE_TC")
P1_DUP=$(teap -o json case duplicate --file-path "$BASE_TC")
P2_DUP=$(teap -o json case duplicate --file-path "$BASE_TC")
P0_TC=$(teap -o json case import "$(printf '%s' "$P0_DUP" | jq -er '.server_input_path')" | jq -er '.path')
P1_TC=$(teap -o json case import "$(printf '%s' "$P1_DUP" | jq -er '.server_input_path')" | jq -er '.path')
P2_TC=$(teap -o json case import "$(printf '%s' "$P2_DUP" | jq -er '.server_input_path')" | jq -er '.path')
```

若响应 `editable=true`，直接使用 `server_input_path`，不要再次 import。取得三个 `.tc` 后分别修改、回读、`case validate` 和启动任务。

禁止用 `result download-tc`、`file/case/result download`、本地 `cp`、重新上传完成历史输入恢复或方案分叉。只有用户明确要求本地文件时才下载。`retryable=false` 后停止等价重试并执行 hint。
