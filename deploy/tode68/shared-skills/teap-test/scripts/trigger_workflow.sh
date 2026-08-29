#!/bin/bash
#
# 触发 Gitea workflow dispatch
# 用法: ./trigger_workflow.sh <workflow_id> <branch> [inputs_json]
# workflow_id: workflow 文件名或 ID
# branch: 分支名
# inputs_json: 可选，JSON 格式的 inputs 参数
#

GITEA_HOST="https://gitea.tode.ltd"
REPO_OWNER="tode"
REPO_NAME="teap-integration-test"
TOKEN="${GITEA_TOKEN:-}"

if [ -z "$TOKEN" ]; then
  echo "ERROR: GITEA_TOKEN 环境变量未设置，请提供 Gitea Personal Access Token"
  exit 1
fi

if [ $# -lt 2 ]; then
  echo "ERROR: 缺少必要参数。用法: $0 <workflow_id> <branch> [inputs_json]"
  exit 1
fi

WORKFLOW_ID="$1"
BRANCH="$2"
INPUTS_JSON="${3:-}"

API_URL="${GITEA_HOST}/api/v1/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_ID}/dispatches"

# 构建请求体
if [ -n "$INPUTS_JSON" ]; then
  BODY=$(cat <<EOF
{
  "ref": "${BRANCH}",
  "inputs": ${INPUTS_JSON}
}
EOF
)
else
  BODY=$(cat <<EOF
{
  "ref": "${BRANCH}"
}
EOF
)
fi

# 触发 workflow
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${BODY}" \
  "${API_URL}")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY_CONTENT=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "204" ]; then
  echo "SUCCESS: Workflow 触发成功 (workflow: ${WORKFLOW_ID}, branch: ${BRANCH})"

  # 获取最新的 run 信息
  sleep 2
  RUNS_URL="${GITEA_HOST}/api/v1/repos/${REPO_OWNER}/${REPO_NAME}/actions/runs?workflow_id=${WORKFLOW_ID}&page=1&limit=1"
  RUN_INFO=$(curl -s -H "Authorization: Bearer ${TOKEN}" "${RUNS_URL}")

  echo "---RUN_INFO---"
  echo "$RUN_INFO"
elif [ "$HTTP_CODE" = "401" ]; then
  echo "ERROR: 认证失败，请检查 GITEA_TOKEN 是否有效"
  exit 1
elif [ "$HTTP_CODE" = "403" ]; then
  echo "ERROR: 无权限，可能 token 缺少必要权限（repo 或 workflow 范围）"
  exit 1
elif [ "$HTTP_CODE" = "404" ]; then
  echo "ERROR: Workflow '${WORKFLOW_ID}' 不存在，请检查名称是否正确"
  exit 1
elif [ "$HTTP_CODE" = "422" ]; then
  echo "ERROR: Workflow 不支持手动触发（workflow 文件中需配置 workflow_dispatch）"
  exit 1
else
  echo "ERROR: 请求失败，HTTP 状态码: ${HTTP_CODE}"
  echo "响应内容: ${BODY_CONTENT}"
  exit 1
fi
