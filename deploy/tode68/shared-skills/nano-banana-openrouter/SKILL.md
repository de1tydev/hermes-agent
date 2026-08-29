---
name: nano-banana-openrouter
description: 通过 OpenRouter Images API 使用支持图片输出的模型生成图片。
platforms: [linux]
prerequisites:
  commands: [python3]
  env_vars: [OPENROUTER_API_KEY]
required_environment_variables:
  - name: OPENROUTER_API_KEY
    prompt: OpenRouter API key
metadata:
  hermes:
    tags: [Image, OpenRouter, Generation]
---

# Nano Banana OpenRouter

## When to Use

用户明确要求通过 OpenRouter 或 Nano Banana 模型生成图片时使用。密钥只从当前 Hermes Profile 的 `OPENROUTER_API_KEY` 读取。

## Usage

```bash
python3 {baseDir}/scripts/generate.py \
  "一只在太空漂浮的红熊猫，电影灯光" \
  "./openrouter-image.png" \
  --aspect-ratio 16:9 \
  --resolution 2K
```

脚本使用 OpenRouter 官方 `/api/v1/images` 接口，返回 `IMAGE_PATH`。生成后通过当前平台的媒体发送能力交付文件；在飞书中不要只输出 `MEDIA:` 文本。

模型能力和可用参数会变化。若默认模型不可用，先查询 `GET https://openrouter.ai/api/v1/images/models`，再通过 `--model` 指定支持图片输出的模型。
