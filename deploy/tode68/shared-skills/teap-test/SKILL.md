---
name: teap-test
description: 触发 Gitea 仓库 tode/teap-integration-test 中的 workflow。用户指定 workflow 名称、branch、输入参数等，skill 调用 Gitea API 触发 workflow dispatch 并返回结果。适用于手动触发 CI/CD 测试、集成测试验证等场景。
platforms: [linux]
prerequisites:
  commands: [bash, curl]
  env_vars: [GITEA_TOKEN]
required_environment_variables:
  - name: GITEA_TOKEN
    prompt: TODE Gitea personal access token
---

# teap-test Skill

## 快速开始

用户提供以下参数：
- **workflow 名称或文件名**（必填，如 `ci.yml`、`integration-test.yml`）
- **branch**（必填，如 `main`、`develop`）
- **输入参数**（可选，workflow 所需的 `inputs` 参数）

调用 Gitea Actions API 触发 workflow，返回运行状态和结果。

## Gitea API 触发 Workflow Dispatch

**Endpoint：**
```
POST https://gitea.tode.ltd/api/v1/repos/tode/teap-integration-test/actions/workflows/{workflow_id}/dispatches
```

**认证方式：** 使用 Gitea Personal Access Token（通过 `GIETA_TOKEN` 环境变量或用户直接提供）

**请求头：**
```
Authorization: Bearer {token}
Content-Type: application/json
```

**请求体：**
```json
{
  "ref": "branch名称",
  "inputs": {
    "key": "value"
  }
}
```

> 注意：`inputs` 字段仅在 workflow 定义了 `workflow_dispatch` 触发器且配置了 `inputs` 时才需要。

## 判断 workflow_id

`workflow_id` 可以是：
- workflow 文件名（如 `ci.yml`）
- workflow 的 ID（数字）

用户通常提供 workflow 名称或文件名，直接使用即可。

## 执行流程

1. 解析用户提供的 workflow 名称和 branch
2. 调用 `POST /repos/tode/teap-integration-test/actions/workflows/{workflow_id}/dispatches`
3. 若触发成功，再调用 `GET /repos/tode/teap-integration-test/actions/runs` 获取最新一条 run 的状态
4. 将结果返回给用户

## 返回内容

- 触发是否成功
- 触发后获取到的 run ID、状态、链接

## 错误处理

- 401/403：无权限，需用户提供有效 token
- 404：workflow 不存在，检查 workflow 名称是否正确
- 422：workflow 不支持 dispatch 触发（需在 workflow 文件中配置 `workflow_dispatch`）
- 网络错误：提示重试

## 脚本

触发 workflow 的核心逻辑封装在 `{baseDir}/scripts/trigger_workflow.sh` 中：

```bash
bash {baseDir}/scripts/trigger_workflow.sh <workflow_id> <branch> '[inputs JSON]'
```
