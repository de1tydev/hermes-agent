# TODE68 Hermes 多 Profile 部署

该部署保留两个 Gateway 容器定义和两套飞书应用凭据，共享 40 条聊天身份路由与 44 个隔离 Profile。当前生产只运行 primary；secondary 已停止，避免两个 PID namespace 争抢同一 Profile 会话租约。飞书 transport 凭据只存在于 `/srv/hermes/.env` 和 `/srv/hermes/gateway-secondary.env`，不会复制到 Profile。

跨 Profile 访问使用两层强制隔离：Hermes 原生工具入口仅按飞书
`user_id=cfgg8ef2` 识别管理员；其他用户的终端、`execute_code` 和验证子进程
由 Linux Landlock 限制为当前 Profile 可读写、共享 Skill/二进制只读。普通用户
即使传入 `cross_profile=True`、拼接路径、使用符号链接或修改自己的
`state.db`，也不能读取、写入或枚举其他 Profile。

`tode68-20260831-profileiso2` 只在上一版隔离镜像上为 Landlock 增加当前沙箱进程 `/proc/self` 的只读规则，使 CoreCLR 等 self-contained managed runtime 可以读取自身映射。它不开放整个 `/proc`；`/proc/self/root`、`/proc/self/cwd` 仍不能绕过跨 Profile 边界。

切换前必须确认：

- `/srv/hermes/migration/external-memory-disabled.json` 的 `passed` 为 `true`；
- `state/profile-identity-registry.json` 有 40 条 route；
- 44 个 Profile 的 `memories/MEMORY.md`、`memories/USER.md`、workspace、Skill 和只读 legacy archive 完整；
- `openclaw-gateway` 与两个 Hermes Gateway 不得同时连接相同飞书应用。

primary 生产启动：

```bash
docker start hermes-gateway-primary
```

## 选择性代理

宿主机 Docker daemon 的代理只用于拉取镜像；不要在
`~/.docker/config.json` 中配置 `proxies.default`，否则 Docker CLI 会把代理变量
注入所有新容器。Hermes 只连接本机 Mihomo，Mihomo 按域名规则决定直连或转发到
现有 `192.168.50.12:7890` 上游：

```bash
docker compose -f /opt/hermes-agent/deploy/tode68/mihomo/compose.yaml up -d
```

规则位于 `deploy/tode68/mihomo/config.yaml`：飞书、TODE 内网和私网显式直连；
MetaCubeX `meta-rules-dat` 的 `gfw.mrs` 黑名单命中项走上游，规则每 24 小时
从 GitHub 更新；未命中的目标默认直连。Mihomo 只监听 `127.0.0.1:17890`，
不启用 TUN、透明代理或 LAN 监听。

宿主机使用 `systemd-resolved` 时，`/etc/resolv.conf` 必须是指向
`/run/systemd/resolve/stub-resolv.conf` 的符号链接。不要复制该文件为普通文件；
否则 `search .` 可能被 foreign 模式读成全局根域路由，令查询绕过网卡 DNS
`192.168.50.1` 而落到不稳定的公网 fallback DNS。上线前确认
`resolvectl status` 显示 `resolv.conf mode: stub`，且不存在 Global `~.`。

root `config.yaml` 的 `platforms.feishu.extra` 使用
`http_timeout_seconds: 180` 和 `file_upload_attempts: 3`。附件上传网络超时会重新
打开文件并有限重试；飞书返回的确定性 API 错误不会重试。

## 飞书群共享会话

TODE68 保持 `gateway.group_sessions_per_user: false`，同一飞书群的所有成员共享
一条群级 Hermes session。普通群中的引用回复只携带
`reply_to_message_id`/`reply_to_text`，用于保留引用内容和回复样式；普通回复的
`root_id` 不得作为 session key 的 thread 段，否则同一群会被静默拆成多个
会话。只有飞书真正的 `thread_id`，以及 TOPIC/forum 群缺少显式 thread 时的
`root_id` fallback，才建立独立 thread session。

因此普通群的共享 session key 固定为：

```text
agent:<profile>:feishu:group:<chat_id>
```

## NewAPI 会话亲和

TODE68 镜像通过 `deploy/tode68/model_providers/tode` 为
`https://newapi.tode.ltd/v1` 的模型请求注入会话级软亲和请求头：

- `X-NewAPI-Affinity-Conversation` 使用 Hermes 持久会话的 conversation
  root 生成不透明摘要；同一会话的工具回合、重试、辅助模型调用和压缩轮转保持
  不变，不同会话使用不同值；
- 并行 `delegate_task` 子 Agent 复用父会话 conversation，并以自身稳定 session
  生成 `X-NewAPI-Affinity-Lane`；
- 亲和键不包含飞书用户、群聊、API Key、提示词或 NewAPI 渠道 ID，且不会发送给
  `newapi.tode.ltd` 以外的端点；
- `429` 继续由 Hermes 外层重试读取 `Retry-After` 并退避，重试不更换亲和键。

该 provider adapter 同时覆盖主聊天和 compression、title、vision 等统一辅助模型
调用。上线前应确认 NewAPI 对目标模型启用了 `stateful_v2`，并在至少 20 个不同
session 的并发联调中检查 bucket 分布和渠道并发上限。

上线前用 `tests/gateway/test_feishu_session_scope.py` 重放“回复附件发起任务、顶层
追问进展、继续引用回复”的路由模式，并确认普通回复与群顶层消息 key 相同、真实
TOPIC/forum 仍按 thread 隔离。

secondary 通过 Compose profile 隔离，普通 `docker compose up -d` 不会启动它。只有明确恢复第二个 Gateway 时才使用 `docker compose --profile secondary up -d gateway-secondary`；恢复前仍需先解决共享 Profile 会话租约问题。

## Profile 内命令

容器由 root 启动 s6，但 Gateway 和 Profile 运行时以 `10000:10000` 写入
`/opt/data/profiles/<profile>`。手工运行会写 Profile 状态的命令时必须显式使用
该 UID/GID；默认 root 身份的 `docker exec` 会留下 Scheduler 无法更新的
`root:root` 日志、数据库、缓存或输出文件。

例如手工触发 Profile 定时任务：

```bash
docker exec --user 10000:10000 \
  -e HERMES_HOME=/opt/data/profiles/<profile> \
  hermes-gateway-primary \
  /opt/hermes/.venv/bin/hermes cron run <job_id>
```

执行后检查所有 Profile 的 owner 漂移；命令应无输出：

```bash
find /srv/hermes/profiles -xdev \
  \( ! -uid 10000 -o ! -gid 10000 \) -print
```

回滚：

```bash
docker compose -f /opt/hermes-agent/deploy/tode68/compose.yaml down
docker start openclaw-gateway
```

原 `/srv/openclaw` 和 `/opt/openclaw-docker/compose.yaml` 不修改；只读冻结副本保存在 `/srv/hermes-backups/openclaw-20260829-pre-hermes-cutover`，至少保留四周。

## Skill 兼容化

原迁移清单中进入 review 区的 17 个 Skill 已在 `shared-skills/` 中完成 Hermes 适配。生产修复由 `scripts/remediate_tode68_skills.py` 执行，负责：

- 去除 OpenClaw 专属路径、软链接、虚拟环境、依赖缓存和内嵌凭据文件；
- 将第三方 API Key 改为当前 Profile 的 `required_environment_variables`；
- 把 Skill 同步到 canonical shared root 和所有现有 Profile；
- 安装 `teap`、`jira`、`lark-cli` 到 `/srv/hermes/bin`；
- 关闭 `/new`、`/reset`、`/clear`、`/undo` 的额外确认提示；
- 飞书只发送最终答复，关闭工具进度、reasoning、commentary、流式草稿和长任务心跳；
- 每个已路由单聊或群聊以自身作为该 Profile 的 Home Channel；新聊天自动建 Profile 时同步写入，无需手工 `/sethome`；
- 使用 `scripts/migrate_tode68_openclaw_cron.py` 将 OpenClaw 用户/业务定时任务按路由导入各 Profile；保留启停状态，不迁移执行历史，OpenClaw 内部维护任务进入审计清单而不继续执行；
- 使用 `scripts/reconcile_tode68_dm_profiles.py` 将旧 open_id 按飞书应用转换为一次性 DM 身份别名，首次联系后永久绑定 chat_id；同一成员在不同机器人聊天中仍保持不同 Profile。脚本同时为所有 Profile 配置原生智谱 Search、Reader、Zread MCP，并让新 Profile 安全继承 `ZHIPU_API_KEY`；
- 生成 `/srv/hermes/migration/skill-remediation-receipt.json`。

旧环境没有配置 `GEMINI_API_KEY`、`DASHSCOPE_API_KEY`、`EVOLINK_API_KEY` 时，对应图片 Skill 会保留并显示缺少配置，不会伪造或复用其他 Provider 的凭据。

## 外挂 personal Skill 完整性修复

旧 OpenClaw 还把宿主机 `/home/liaoshiwu/.agents/skills` 挂载到容器。该目录不在 `/srv/openclaw/skills` 和 `/srv/openclaw/workspace/skills` 内，原迁移器因此漏掉 `ask-code` 和 23 个 `lark-*` Skill。

- 完整迁移时通过 `migrate_tode68_openclaw.py --personal-skills-root <目录>` 显式纳入外挂 Skill；不能依赖容器 mount 被自动发现。
- 现网补迁使用 `scripts/repair_tode68_personal_skills.py`，只安装 `deploy/tode68/personal-skills/` 中经过检查的 24 个 Skill，并同步 canonical shared root 和全部现有 Profile；同时把 `deploy/tode68/runtime-skills/officecli` 与匹配的 `officecli` 二进制同步到所有 Profile 和隔离策略允许只读执行的 `/opt/data/bin`。
- Ask Code 的 `ASK_CODE_URL`、`ASK_CODE_API_KEY` 从旧 personal Skill 私有 `.env` 读取，只写入各 Profile 的私有 `.env`；`.env`、状态文件和备份不会进入 Skill 树。
- Lark 用户认证继续使用当前 Profile workspace 下的 `agent-data/lark-cli/`，每次通过 `LARKSUITE_CLI_CONFIG_DIR` 显式选择；不得共享其他 Profile 的用户授权，也不得把 Gateway transport 凭据复制到 Profile。
- 当前生产镜像不含 ICU；修复器为 Profile 写入 `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1`，使 self-contained `officecli` 使用 .NET 官方支持的 invariant globalization 模式启动，无需扩大镜像或隔离白名单。
- 修复前备份位于 `/srv/hermes/backups/personal-skill-repair-*`，回执位于 `/srv/hermes/migration/personal-skill-repair-receipt.json`。
