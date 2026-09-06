#!/bin/bash
# 启动本地 WeMM embedding 服务（llama.cpp llama-server，OpenAI 兼容，端口 1235，无需鉴权）。
#
# 背景：LM Studio 0.4.x 把 WeMM 的 GGUF（arch=qwen35）归类为 LLM 而非 embedding
# （架构白名单未收录），其 /v1/embeddings 无法服务该模型；且 LM Studio 开启 API
# token 鉴权后所有请求需要 Bearer token。因此本地 embedding 由 llama-server
# 单独供在这台机器上，与 LM Studio（LLM，端口 1234）互不干扰。
#
# 开机后如需向量检索/文档上传，先跑本脚本（或加入 launchd 自启）。
GGUF="$HOME/.lmstudio/models/Weidows/WeMM-Embedding-2B-GGUF/WeMM-Embedding-2B-Q5_K_M.gguf"
exec /opt/homebrew/bin/llama-server -m "$GGUF" \
  --embedding --pooling last --alias wemm-embedding-2b \
  --host 127.0.0.1 --port 1235 -ngl 999 -c 8192
