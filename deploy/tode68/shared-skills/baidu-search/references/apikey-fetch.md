# Baidu API Key 配置

## BAIDU_API_KEY Not Configured

当 `BAIDU_API_KEY` 未配置时，按下面步骤处理。

### 1. 获取 API Key

访问 <https://console.bce.baidu.com/ai-search/qianfan/ais/console/apiKey>，创建或查看 AI Search API Key。

### 2. 配置 Hermes Profile

将密钥写入当前 Hermes Profile 的 `.env`。不要写入 Skill、脚本、命令行参数或聊天消息。

```dotenv
BAIDU_API_KEY=replace-with-real-value
```

### 3. 验证

```bash
python3 {baseDir}/scripts/search.py '{"query":"test search"}'
```

## Troubleshooting
- 确认 API Key 有效且已开通百度 AI Search。
- 检查百度智能云账户额度。
- 禁止在诊断输出中打印密钥。
