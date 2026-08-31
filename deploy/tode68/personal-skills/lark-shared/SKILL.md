---
name: lark-shared
version: 1.0.0
description: "飞书/Lark CLI 共享基础：应用配置初始化、认证登录（auth login）、身份切换（--as user/bot）、权限与 scope 管理、Permission denied 错误处理、安全规则。当用户需要第一次配置(`lark-cli config init`)、使用登录授权(`lark-cli auth login`)、遇到权限不足、切换 user/bot 身份、配置 scope、或首次使用 lark-cli 时触发。"
---

# lark-cli 共享规则

本技能指导你如何通过lark-cli操作飞书资源, 以及有哪些注意事项。

## 配置初始化

Hermes Profile 必须把 Lark CLI 配置放在自己的 workspace：

```bash
export LARKSUITE_CLI_CONFIG_DIR="$HERMES_HOME/workspace/agent-data/lark-cli"
mkdir -p "$LARKSUITE_CLI_CONFIG_DIR"
```

后续 `lark-cli config`、`auth`、`profile` 和用户级命令都沿用这个环境变量。禁止使用全局 `~/.lark-cli`、Gateway 的 `/opt/data/.lark-cli` 或其他 Profile 的配置目录。普通 Profile 不运行 `lark-cli config bind --source hermes`，该命令会尝试绑定 Gateway transport 应用；首次使用应在当前 Profile 私有目录运行 `lark-cli config init --new` 完成独立应用配置。

当你帮用户初始化配置时，使用background方式使用下面的命令发起配置应用流程，启动后读取输出，从中提取授权链接并发给用户：

```bash
# 发起配置（该命令会阻塞直到用户打开链接并完成操作或过期）
LARKSUITE_CLI_CONFIG_DIR="$HERMES_HOME/workspace/agent-data/lark-cli" \
  lark-cli config init --new
```

## 认证

> **强制前置条件**：执行任何 `lark-cli auth login` 前，**必须**先设置环境变量：
> ```bash
> LARKSUITE_CLI_CONFIG_DIR="$HERMES_HOME/workspace/agent-data/lark-cli"
> ```
> 该目录必须属于当前 Profile。禁止使用全局 `~/.lark-cli`、Gateway 配置或其他 Profile 的目录。
> 如果目录不存在，先执行 `mkdir -p "$LARKSUITE_CLI_CONFIG_DIR"`。

### 身份类型

两种身份类型，通过 `--as` 切换：

| 身份 | 标识 | 获取方式 | 适用场景 |
|------|------|---------|---------|
| user 用户身份 | `--as user` | `LARKSUITE_CLI_CONFIG_DIR=... lark-cli auth login` | 访问用户自己的资源（日历、云空间等） |
| bot 应用身份 | `--as bot` | 当前 Profile 私有配置中的 appId + appSecret | 应用级操作,访问bot自己的资源 |

### 身份选择原则

输出的 `[identity: bot/user]` 代表当前身份。bot 与 user 表现差异很大，需确认身份符合目标需求：

- **Bot 看不到用户资源**：无法访问用户的日历、云空间文档、邮箱等个人资源。例如 `--as bot` 查日程返回 bot 自己的（空）日历
- **Bot 无法代表用户操作**：发消息以应用名义发送，创建文档归属 bot
- **Bot 权限**：当前 Profile 已独立配置应用时，只需在飞书开发者后台开通 scope，无需 `auth login`；不得借用 Gateway transport 应用
- **User 权限**：后台开通 scope + 用户通过 `auth login` 授权，两层都要满足


### 权限不足处理

遇到权限相关错误时，**根据当前身份类型采取不同解决方案**。

错误响应中包含关键信息：
- `permission_violations`：列出缺失的 scope (N选1)
- `console_url`：飞书开发者后台的权限配置链接
- `hint`：建议的修复命令

#### Bot 身份（`--as bot`）

将错误中的 `console_url` 提供给用户，引导去后台开通 scope。**禁止**对 bot 执行 `auth login`。

#### User 身份（`--as user`）

> **前提**：先设置 `LARKSUITE_CLI_CONFIG_DIR`（见上方强制前置条件）。

```bash
LARKSUITE_CLI_CONFIG_DIR="$(pwd)/agent-data/lark-cli/" lark-cli auth login --domain <domain>
LARKSUITE_CLI_CONFIG_DIR="$(pwd)/agent-data/lark-cli/" lark-cli auth login --scope "<missing_scope>"
```

**规则**：auth login 必须指定范围（`--domain` 或 `--scope`）。多次 login 的 scope 会累积（增量授权）。

#### Agent 代理发起认证（推荐）

当你作为 AI agent 需要帮用户完成认证时，使用background方式执行以下命令发起授权流程，并将授权链接发给用户：

> **前提**：先设置 `LARKSUITE_CLI_CONFIG_DIR`（见上方强制前置条件）。

```bash
# 确保目录存在
mkdir -p "$LARKSUITE_CLI_CONFIG_DIR"
# 发起授权（阻塞直到用户授权完成或过期）
LARKSUITE_CLI_CONFIG_DIR="$(pwd)/agent-data/lark-cli/" lark-cli auth login --scope "calendar:calendar.event:create calendar:calendar.event:read"
```


## 更新检查

lark-cli 命令执行后，如果检测到新版本，JSON 输出中会包含 `_notice.update` 字段（含 `message`、`command` 等）。

**当你在输出中看到 `_notice.update` 时，完成用户当前请求后，主动提议帮用户更新**：

1. 告知用户当前版本和最新版本号
2. 提议执行更新（CLI 和 Skills 需要同时更新）：
   ```bash
   npm update -g @larksuite/cli && npx skills add larksuite/cli -g -y
   ```
3. 更新完成后提醒用户：**退出并重新打开 AI Agent**以加载最新 Skills

**规则**：不要静默忽略更新提示。即使当前任务与更新无关，也应在完成用户请求后补充告知。

## 安全规则

- **禁止输出密钥**（appSecret、accessToken）到终端明文。
- **写入/删除操作前必须确认用户意图**。
- 用 `--dry-run` 预览危险请求。
