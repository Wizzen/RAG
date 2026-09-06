#!/bin/bash
# 启动本地重排序服务（llama.cpp llama-server --rerank，端口 1236，无需鉴权）。
# 模型：Qwen3-Reranker-0.6B q8（与 LM Studio:1234 / embedding:1235 互不干扰）。
# 注意：llama.cpp /rerank 在部分版本有正确性问题（issue #16407），
# 启用前先用 kb.rerank.sanity_check() 验证（评估页也可观察整体效果）。
GGUF="$HOME/.lmstudio/models/dengcao/Qwen3-Reranker-0.6B-GGUF/Qwen3-Reranker-0.6B-q8_0.gguf"
exec /opt/homebrew/bin/llama-server -m "$GGUF" --rerank --host 127.0.0.1 --port 1236 \
  --alias qwen3-reranker-0.6b -ngl 999 -c 8192
