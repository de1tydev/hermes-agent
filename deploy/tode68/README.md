# TODE68 Hermes 多 Profile 部署

该部署使用两个 Gateway 连接现网已有的两个飞书应用，共享同一套 39 条聊天身份路由与 43 个隔离 Profile。飞书 transport 凭据只存在于 `/srv/hermes/.env` 和 `/srv/hermes/gateway-secondary.env`，不会复制到 Profile。

跨 Profile 访问使用两层强制隔离：Hermes 原生工具入口仅按飞书
`user_id=cfgg8ef2` 识别管理员；其他用户的终端、`execute_code` 和验证子进程
由 Linux Landlock 限制为当前 Profile 可读写、共享 Skill/二进制只读。普通用户
即使传入 `cross_profile=True`、拼接路径、使用符号链接或修改自己的
`state.db`，也不能读取、写入或枚举其他 Profile。

切换前必须确认：

- `/srv/hermes/migration/external-memory-disabled.json` 的 `passed` 为 `true`；
- `state/profile-identity-registry.json` 有 39 条 route；
- 43 个 Profile 的 `memories/MEMORY.md`、`memories/USER.md`、workspace、Skill 和只读 legacy archive 完整；
- `openclaw-gateway` 与两个 Hermes Gateway 不得同时连接相同飞书应用。

生产启动：

```bash
docker compose -f /opt/hermes-agent/deploy/tode68/compose.yaml up -d
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
