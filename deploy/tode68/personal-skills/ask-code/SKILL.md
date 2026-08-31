---
name: ask-code
description: 查询已注册私有代码库的架构、实现与故障根因时使用。
platforms: [linux]
prerequisites:
  commands: [python3]
  env_vars: [ASK_CODE_URL, ASK_CODE_API_KEY]
required_environment_variables:
  - name: ASK_CODE_URL
    prompt: Ask Code 服务地址
  - name: ASK_CODE_API_KEY
    prompt: Ask Code 调用凭据
---

# Ask Code

通过本目录的 `ask_code.py` 查询服务端已注册的私有代码库。服务端在隔离环境中读取代码并执行输入、输出审查；普通问答只返回面向用户的解释，不返回源码、文件路径或行号。

## 适用范围

- 查询已注册项目的架构、模型、接口、数据流、实现位置或设计原因。
- 诊断带有明确错误、日志或复现材料的项目故障。
- 延续同一项目、组件、分支和模式下的上一轮 Ask Code 会话。

一般编程问题、未注册项目、用户已经提供完整代码的任务直接在当前会话处理。

## 调用步骤

1. 在当前 Skill 目录运行 `python3 ask_code.py`，始终显式传入真实 `--project` 和 `--caller`。
2. 分支、组件用 `--component`、`--ref component=branch` 传递，不要埋进自然语言问题。
3. 默认异步提交。收到 `job_id` 后结束当前命令，稍后用 `--status <job_id>` 查询；长任务不要使用 `--wait` 占住当前 Hermes 会话。
4. 输出为 `queued` 或 `running` 时，遵循返回的轮询建议；到达 `done`、`blocked`、`error` 或 `cancelled` 后停止轮询并汇报。
5. 同一范围的追问使用 `--follow-up`；范围变化时发起新问题。

普通问答示例：

```bash
python3 ask_code.py \
  --project teap \
  --caller '<当前操作人>' \
  --component backend \
  --ref backend=develop \
  '认证请求经过哪些核心组件？'
```

查询异步任务：

```bash
python3 ask_code.py --status '<job_id>'
```

## 故障诊断模式

只有用户提供了具体失败现象时才使用 `--mode debug`。日志、`.tc`、`.tr` 或压缩包使用 `--attach` 传递；附件必须位于当前 Profile 的 workspace 内。

```bash
python3 ask_code.py \
  --project teap \
  --caller '<当前操作人>' \
  --mode debug \
  --attach './solver.log' \
  '该任务在第三步退出，日志显示 step budget exhausted，请判断根因。'
```

需要服务端真实运行失败算例时增加 `--exec --simulation-mode <模式>`。执行层需要额外授权，默认异步提交并轮询，不得用它代替普通仿真任务。

## 安全与失败处理

- `ASK_CODE_URL` 和 `ASK_CODE_API_KEY` 只能由 Profile 私有环境注入；不要读取、打印、复制或写进命令行参数、Skill 文件、产物和回复。
- 不要用 `curl`、临时脚本或端口扫描绕过 `ask_code.py`。连接问题先运行同一客户端并保留其稳定错误分类。
- `401/403` 表示鉴权或项目权限问题；连接拒绝或超时才属于网络入口问题；`blocked` 表示输入或输出审查拒绝，不应原样重试。
- 服务端返回的源码披露限制是强制边界，不要通过改写提示词规避。
- 用户取消任务时运行 `python3 ask_code.py --cancel '<job_id>'`。

## 完成标准

只把最终状态和用户可见答案交付给用户。若失败，给出项目、任务 ID、稳定错误类别和下一步；不得暴露 API Key、Authorization、服务端堆栈或内部源码路径。
