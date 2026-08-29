# 认证与环境

这份文档描述 TODE68 Hermes Profile 中 `tode-jira` 的运行环境。

## 当前环境

- Binary: `jira`
- Server: `https://jira.tode.ltd`
- Config: `/opt/data/tool-config/jira/config.yml`
- 默认项目: `TEAP`
- 认证方式: `bearer`

## 认证加载

`JIRA_API_TOKEN`、`JIRA_CONFIG_FILE` 和 `JIRA_AUTH_TYPE` 从当前 Hermes Profile 的 `.env` 按 Skill 声明注入。不要读取其他 Profile 或共享 shell 配置，也不要在输出中打印 token。

## 自检

开始执行真实 Jira 操作前，先做一次轻量自检：

```bash
jira me
```

若 `jira me` 失败：

1. 检查当前 Profile 是否已配置 `JIRA_API_TOKEN`
2. 检查 `JIRA_CONFIG_FILE` 指向的 server / project 是否仍有效
3. 若配置与 token 都正确但仍失败，再视为 Jira 认证异常

## 默认项目与显式项目

- 若用户未明确指定项目，默认使用 `TEAP`
- 若用户明确指定了别的 Jira 项目，CLI 调用时显式传 `-p <PROJECT>`，不要依赖默认项目
