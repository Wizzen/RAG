# 对象知识抽取对照（Cognee PoC）

只比较已选正文上的抽取结构和内容，不执行 Cognee ingestion、embedding 或正式图谱入库。
Cognee 使用独立环境中的真实 `extract_content_graph`、`DataPoint`、`FromIdentity` 和身份生成逻辑。
生产依赖文件不增加 Cognee，运行中的 RAG 不导入这些实验组件。

## 环境与本地检查

```powershell
uv venv output/cognee-poc-env --python 3.13
uv pip install --python output/cognee-poc-env/Scripts/python.exe cognee==1.6.5
.venv/Scripts/python.exe scripts/graph_benchmark/run.py --backend smoke
.venv/Scripts/python.exe manage.py test kb.tests.test_graph_benchmark
```

固定正文 fixture 放在 `output/cognee-comparison/fixture.json`，含 document_id、document、source_digest、focus、chunks。
每段正文保留当前索引的 chunk_id、text 及必要表头。两个实现接收同一份固定正文；不比较召回质量，也不启动全文扫描。

## 获得具体正文和模型目的地址的发送授权后

```powershell
.venv/Scripts/python.exe scripts/graph_benchmark/run.py --backend both
.venv/Scripts/python.exe scripts/graph_benchmark/compare.py
```

如果独立环境的完整 SDK 下载失败，可以显式选择 `--backend schema`，使用已有 OpenAI 客户端和 Pydantic
对 Cognee 文档中的类型化节点、父引用和结构化输出方案做实验。此模式写入 `cognee-schema-result.json` 和
披露文件，不写原生 SDK 的 `cognee.json`；报告标为 `schema_pattern`，不得当作 Cognee SDK 的性能或集成结果。
它与原生 SDK 共用 cognee 侧的两次额度，不会因切换模式额外增加模型调用。

每个实现最多2次模型请求，每次最多32768输出 tokens，代理总期限600秒。
代理使用站点配置的模型，真实凭据只在父进程内存中；Cognee 进程仅能访问本机代理，不获得真实密钥。
SDK 的内部修复/重试也受代理总次数限制。请求、响应、耗时及模型实际返回的 usage 单独保存，无法获取 token 数时不伪造成本。
请求额度在发送前持久化；重启脚本不重置，同一结果目录中的已完成产物直接复用。
未配置价格，因此不把 token 数换算为金额。

## 模型与审核边界

领域模型只规定对象、参数、分类项、子参数、内容和来源等通用角色。实际属性名随原文扩充。
使用带完整路径的 key 和 `FromIdentity` 父引用表达任意层级，避免同名 I 级串对象；循环和悬空引用明确拒绝。
节点的每条内容独立保留证据、条件、比较符和单位。公式保留原文，不做没有证据的数值换算。
转换结果再通过现有 V2 逐字来源与路径校验。检查同时报告原始输出质量和过滤后质量，不能靠丢弃错误掩饰抽取问题。

当前对照表针对 GB/T 34370.5-2017 表9人工核对的13个非空取值单元格，以及近场条件、独立分级、危害性缺陷。
内容覆盖和结构粒度分别计分：正确取值即使嵌在内容对象字段中也计为覆盖，但不计为独立参数节点。
条件检查读取结构化内容及条件字段，不因原文引文碰巧出现相应词语而判定条件已经建模。
表格匹配采用保守的表达式比对，逐项值保留在 comparison.json 供复核；这不是完整语义评测，也不能代表所有文档的准确率。
结构通过、原文匹配或 SDK 校验均不等同于人工确认；所有实验结果均为未审核候选，不参与问答。

## 已定位表格的无模型调用对照

```powershell
.venv/Scripts/python.exe scripts/graph_benchmark/table_geometry.py
.venv/Scripts/python.exe scripts/graph_benchmark/compare.py
```

此样例复用保存的模型结果中的片段位置，按真实表头列和参数行建立等级、参数及取值，保留完整原行与表头作为证据。
对象与行列方向针对该样本明确给定，没有把阈值写死在代码中；遇到多个表格或行列不齐时停止，不猜测。
编号条款按原文作为说明保存，尚未自动推断每项条件的逻辑作用范围；跨页表格、合并单元格和其它行列方向仍需适配。
这一步不需要新增 LLM 调用，不等同于通用知识图谱构建已经解决。

完整原始和通过校验的树形对照见 `output/cognee-comparison/comparison.html`；可独立分享的页面另存为 `view/index.html`。
