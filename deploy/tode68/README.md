# TODE68 Hermes 多 Profile 部署

该部署使用两个 Gateway 连接现网已有的两个飞书应用，共享同一套 39 条聊天身份路由与 43 个隔离 Profile。飞书 transport 凭据只存在于 `/srv/hermes/.env` 和 `/srv/hermes/gateway-secondary.env`，不会复制到 Profile。

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
