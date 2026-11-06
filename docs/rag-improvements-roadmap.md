# RAG 能力对标与改进路线（调研结论）

> 2026-09 调研结论，供后续开发/agent 参考。对标对象：GitHub 高星 RAG 项目
> （RAGFlow ~70k★、Dify ~114k★、LightRAG、AnythingLLM、kotaemon）。
> 原始调研来源见文末。
>
> **实施状态（2026-09-06 更新）：P0 / P0.5 / P1 已全部上线**，详见下文 ✅ 标记。

---

## 一、项目现状定位（已完成，勿重复建设）

| 能力 | 本项目实现 | 对标结论 |
|---|---|---|
| Agentic RAG | LangGraph agent（`kb/agent.py`）：自主选库 + kb_fetch_doc 多轮取文档 + 预算控制 | **领先**：多数平台仍是固定管线 |
| 向量隔离 | 每文档独立 Chroma collection（`data/chroma/<slug>/`） | 比 RAGFlow 的 dataset 级更细 |
| 文档解析 | MinerU OCR（PDF→MD）+ `_md_for_embedding`（HTML 表格→结构化文本） | 与 RAGFlow DeepDoc 同级；表格处理已做 |
| Embedding | 本地 WeMM-2B（llama-server:1235，OpenAI 兼容）+ 配置预设切换 | 领先；多模态就绪（图片检索待做） |
| 多租户 | 部门 ACL + 三级账号（全局/部门管理员/普通） | 比 AnythingLLM workspace 粒度细 |
| 业务关联 | 图纸号跨表关联（`kb/structured_data.py`）+ 检查项提取（`kb/inspection.py`） | **差异化**：开源项目均无 |
| 端口配置 | LLM=LM Studio:1234；Embedding=llama-server:1235；均在 `/kb/settings/` 可切换 | — |

关键机制备忘：
- 换 embedding 模型后必须 `python manage.py reindex_clean`（维度+向量空间不可比），
  `kb/retriever.py` 有配置指纹缓存失效，管理页有 stale 警告横幅。
- 搜索结果的「chip 双锚点跳转高亮」（`document_html?h=...&h=短锚点`，
  `kb/inspection.py` 的 highlight/highlight_short）——**改进项 P1 可直接复用**。
- 部署环境：macOS 本机，HF 直连不通需走 hf-mirror.com；LM Studio 对非白名单
  GGUF 架构（如 WeMM 的 qwen35）不识别为 embedding，本地向量/重排一律走 llama-server。

## 二、待引入能力（按优先级）

### ✅ P0 混合检索（向量 + 关键词 RRF）— 已上线（2026-09-06）

- **实现**：`kb/keyword_index.py`（SQLite `data/keyword.db`，与 Chroma 双写双删）
  + `kb/retriever.py:search()`（双路召回 → RRF 融合，`via` 字段标注命中路径 vec/kw）。
- **与原方案的偏差**：没用 FTS5——其分词器对中文（unicode61 连续 CJK 单 token）和
  短词（trigram ≥3 字符）都有坑；本项目规模（每库 ≤ 数千 chunk）用 LIKE 预过滤 +
  Python 打分（词频 × IDF）是毫秒级。**词项切分**：字母数字保留 `-_.` 连接
  （图纸号 `61250-56-0011` 整体匹配），CJK 串切二元组。
- **钩子**：`run_indexing` 双写、`delete_doc_vectors` 双删、`reindex_clean` 清库。
  存量数据回填：`python manage.py keyword_backfill`。
- **注意**：RRF 融合键 = chunk 全文 md5（不能用前缀——表格 chunk 常共享开头，
  会把不同块错误合并）；同路重复文本只计首次（分块器会产生内容相同的 chunk）。
- **效果**：编号类查询（`61250-56-0011`）从纯向量的无关命中 → 精确命中目标行。

### ✅ P0.5 重排序 Rerank — 已上线（2026-09-06，云端默认启用）

- **实现**：`kb/rerank.py`（Jina/OpenAI 兼容 `/v1/rerank` 客户端 + sanity_check）
  接入 `search()`（融合序 top 候选 → 精排 → top_k；失败自动降级融合序）。
  设置页「重排序」行可编辑/配置预设；`via` 标记 `+rr`、附 `rerank_score`。
- **当前配置**：SiliconFlow 云端 `BAAI/bge-reranker-v2-m3`（key 空时自动复用
  embedding 的 key），sanity 实测相关句 0.95 / 无关句 0.00。
- **⚠️ 本地路线受阻**：llama.cpp（0.4.0 build 10809）的 `--rerank` 端点对
  Qwen3-Reranker 输出全错（分数 1e-6 量级、排序颠倒，issue #16407 实锤，
  `--pooling rank` 也无效）。模型已下载（dengcao/Qwen3-Reranker-0.6B-q8 GGUF）、
  启动脚本已备（`scripts/start_rerank.sh`，:1236）——上游修复后
  `kb/rerank.sanity_check()` 验证通过即可切换本地。
- **效果**：评估 hit@1 71%（混合无精排）→ **100%**（+rerank）。

### ✅ P1 聊天引用跳转 — 已验证可用（2026-09-06 增强）

- 原有链路（芯片 → `document_html?h=` → 高亮滚动）已存在；本次增强：
  1. `kb/agent.py:_pick_anchor()`：锚点挑选排除「数字-字母-数字」型报告号
     （`25Y0457` 全文 21 处命中，落点在首页），优先部件名/中文短语
     （`barspindle` 唯一命中目标表格行）。
  2. `document_html.html`：smooth scrollIntoView 在部分 webview 不生效 →
     600ms 后校验视口位置，失败用 `scrollTo({behavior:'instant'})` 兜底。

### ✅ P1 检索质量评估面板 — 已上线（2026-09-06）

- `/kb/eval/`（staff，导航「评估」）：问题集 CRUD（问题 + 期望文档 + 期望关键词）
  + 一键运行 → hit@1 / hit@3 + 每题命中明细（含 via 路径与分数）。
- 命令行：`python manage.py eval_retrieval`；模型 `kb.models.EvalQuestion`；
  运行器 `kb/eval.py`（跨全部有向量的文档库，每库独立排名）。
- 已种入 10 个回归问题（编号类 + 语义类混合）。
- **基线记录**：纯向量（换 WeMM 后）未测；混合检索 hit@1 5/7、hit@3 7/7；
  混合+rerank hit@1 7/7、hit@3 7/7。以后任何检索改动跑一遍即可对比。

### P2（后续）

| 项 | 来源 | 说明 |
|---|---|---|
| 知识图谱检索 | LightRAG（双层 low/high-level） | 解决跨文档对比题（"A/B 设备年检差异"）；构建维护成本高，最后做 |
| 多模态检索 | WeMM 图片向量 | vLLM GPU 机器 + mmproj；本机 LM Studio 跑不了 |
| 数据连接器 | Dify/AnythingLLM | SharePoint/网盘批量摄取，运营部需求出现时再做 |
| 摄取管线时间线 | RAGFlow 0.21 | 现有状态轮询够用，补分阶段时间线是小优化 |

## 二点五、Code Review 修复记录（2026-09-06）

子代理评审（P0×1 + P1×5 + P2×14）后修复：

- **P0 XSS**：`document_html` 的 `?h=` 经 `json.dumps|safe` 注入 script 块 →
  改 `|json_script` + 服务端限量（≤12 个 × ≤160 字符），payload 实测中和。
- **P1**：① `manage_delete` 漏清 keyword.db（slug 回收 → 已删内容复活/跨部门泄露）已补；
  ② `search_folder` 丢弃 rerank 排序（最高频路径精排失效）→ 合并键改
  `rerank_score ?? score`；③ rerank key 复用改读**有效**配置（embedding 指向本地时
  回退 .env 云端 key，否则复用占位符会 401）；④ `delete_doc_vectors` 两路清理
  独立 try（Chroma 失败不拖累关键词清理）；⑤ `kb_fetch_doc` N+1（≤160 查询/次）批量化。
- **P2**：keyword_index 连接 `contextlib.closing` + DDL 一次性 + 打分移出锁；
  向量路降级补日志；评估报告 session 瘦身（summary + 每题 top3）；`_pick_anchor`
  全过滤时回退行首（不再返回被排除的报告号）；settings.py 补 `RERANK_*` env 定义；
  `sanity_check` 接入设置页「测试」按钮（target=rerank）。

遗留（未修，低优）：marked.parse 的 raw HTML 透传 XSS 面（预存，建议后续引 DOMPurify）、
eval.html 只展示首个库的行、LLM 输出 innerHTML、eval 同步执行时长、rerank base_url
scheme 限制、`_EMB_FINGERPRINT` 不含 key。

**第二轮复审修复（同日）**：① 设置页测试按钮 fetch 补 CSRF（此前四按钮全 403）；
② **md2html 全面消毒**（存储型 XSS：文本路径先 html.escape、白名单 HTML 剥
on* 事件属性、href/src 与 markdown 链接走 scheme 白名单；`build_doc_html --force`
已重建全部存量 HTML）；③ embedding 缓存指纹含 key 哈希（只换 key 也失效）；
④ delete_doc_vectors 整体兜底（PersistentClient 构造期失败也清关键词）；
⑤ search_folder 分桶排序（有精排分/无精排分分桶，杜绝跨量纲比较）；
⑥ settings 页 effData 改 json_script；⑦ RERANK_ENABLED 死配置移除
（启用只在设置页，.env 不控制，.env.example 已注明）；⑧ siliconflow 判断
大小写不敏感；⑨ rerank 缺 index 条目丢弃；⑩ eval 行带精排分、非法 qid 不 500、
settings_test 死代码清除。

## 三、建议实施顺序

**混合检索 → 引用跳转 → 评估面板 → rerank**（前两项零外部依赖、收益立竿见影；
rerank 放最后等 llama.cpp bug 确认，或先用云端 rerank API 过渡）。

## 四、参考来源

- RAGFlow（infiniflow/ragflow，~70k★）：DeepDoc / 模板分块 / 混合检索 + 重排
- LightRAG（HKUDS）：知识图谱双层检索，GraphRAG 轻量平替
- kotaemon（Cinnamon）：内联引用 + PDF 高亮预览（引用体验标杆）
- Dify（~114k★）：可视化工作流平台；AnythingLLM：workspace 多租户
- 2026 共识栈：智能分块 → 混合召回 → cross-encoder 重排（RAG Best Practices 2026）
- Qwen3-Reranker GGUF：dengcao/Qwen3-Reranker-4B-GGUF（ModelScope）
- llama.cpp rerank bug：github.com/ggml-org/llama.cpp/issues/16407

## 五、E2E 全链路验证记录（2026-09-06，全 SiliconFlow 云端模型）

场景：《中国药典 2025 一部》142MB PDF（用 ingest 项目已解析的 2.2MB 全量 MD 入库，
跳过 MinerU 重解）。配置：embedding=Qwen/Qwen3-Embedding-4B（2560 维）、
LLM=Pro/deepseek-ai/DeepSeek-V3.2、rerank=BAAI/bge-reranker-v2-m3，均已存为
「SiliconFlow」预设（settings 页可一键切回）。

- 入库：HTTP 走 manage 页上传 → 2171 chunks，Chroma 2171 向量（2560 维，KB 徽章
  Qwen3-Embedding-4B）+ keyword.db 2171 行，全程约 70s。
- pipeline 新增远端批量适配：云端端点 embedding 每请求 64 条（langchain 默认 1000
  会超云端批量上限），本地/内网仍 1000。
- 混合检索：语义题（哪种药材有毒→马钱子/山豆根 via=vec+rr）、精确题（麝香用量
  →0.03～0.1g via=kw+vec+rr，精排 0.91）双路均命中。
- 旧 WeMM(2048) 库：向量路维度不匹配自动降级 via=kw+rr（关键词+rerank 不依赖
  embedding），不崩、不影响其它库。想恢复旧库向量检索需按 switch-embedding.md
  重建。评估 hit@1 12/12：旧手册题全靠关键词路保住第一名（精确编号匹配）。
- 药典新增 5 个评估问题全部 ✓@1（三路命中）。
- Agent E2E（40s）：list_knowledge_bases → kb_search×2 → 引用人参/麝香正确章节
  +0.03～0.1g 等高亮锚点。注意：SSE usage 事件里 SiliconFlow 返回的 token 数
  异常偏大（input 4.6M），疑为供应商 usage 口径问题，不影响功能，仅监控时留意。

**修复（2026-09-06 晚）**：chat_stream._prepare 无 kb_slug 时 UnboundLocalError
（ask 页去掉库下拉后，前端 GET /kb/stream/ 不带 kb_slug 成为主路径，而 kb 变量
只在 if kb_slug 分支内赋值）。修复：进分支前初始化 kb = None。教训：E2E 测试
一直显式传 kb_slug，恰好绕开了真实前端请求形态——回归用例应覆盖「页面实际
发送的形状」。
