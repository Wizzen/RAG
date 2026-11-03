# FOS·RAG 项目结构

项目总说明现位于 [README.md](README.md)。首次部署、文件上传、模型接口、问答与引用，以及设备模型和运维工作台的操作步骤都从该页进入。

## 按功能查代码

| 功能 | 入口与职责 |
| --- | --- |
| Web 与认证 | [站点路由](fos_rag/urls.py) 挂载账号、知识库、运行日志及两个工作台。[accounts/](accounts/) 管理账号、部门和角色，[kb/access.py](kb/access.py) 实现知识库的可见和管理范围。 |
| 上传与存储 | [kb/views.py](kb/views.py) 接收文件并按格式分流。[入库队列](kb/ingestion.py) 串行调度手册，[解析与切块](kb/pipeline.py) 生成正文、向量和关键词索引，[业务表导入](kb/structured_data.py) 保存可按键关联的原始行。 |
| 检索与问答 | [检索器](kb/retriever.py) 合并语义、关键词和图谱相关片段。[问答助手](kb/agent.py) 组织工具调用，[后台任务](kb/answer_tasks.py) 管理生成状态，[发布校验](kb/publication.py) 复查引用依据，[会话上下文](kb/history.py) 从持久化消息选择可复用的历史。 |
| 正文与证据 | [文档渲染](kb/md2html.py) 生成阅读视图，[溯源](kb/provenance.py) 将片段关联到原 PDF 页与版面坐标。页面通过受访问控制的文档和证据接口读取原文。 |
| 登记与自动更新 | [追踪表](kb/tracker.py) 按自定义列登记文档，[数据源](kb/feeds.py) 管理推拉批次与发布，[采集助手](kb/connector_agent.py) 试采和验证连接器。 |
| 知识图谱 | [抽取](kb/graph_extraction.py) 从文档生成候选，[分层知识](kb/knowledge_hierarchy.py) 表示对象、属性和带证据的取值，[图谱检索](kb/graph_retrieval.py) 连接已审核事实与来源。 |
| 业务工作台 | [vehicle_tracker/](vehicle_tracker/) 提供通用设备档案、车辆三维追踪及图纸联查。[operations_workbench/](operations_workbench/) 提供 Maximo、Splunk、Downtime 只读查询与运维 AI。两类专用助手通过[独立模型调用](fos_rag/isolated_model.py)读取站点回答配置。 |
| 管理与界面 | [配置读取](kb/config.py) 合并站点与部署配置，[检索评估](kb/eval.py) 计算命中指标，[dashboard/](dashboard/) 汇总运行统计。页面模板在 [templates/](templates/)，交互脚本与样式在 [static/](static/)。 |

## 数据如何对应

- `KnowledgeBase` 表示资料集或文档子库。`Document` 连接上传原件、解析正文、处理状态和片段数量。`ChunkProvenance` 保存可定位片段的页码与区域。
- `StructuredDataset` 和 `StructuredRecord` 保留业务表版本、原始行和关联键。`KbTracker` 和 `TrackerRow` 表示自定义登记表及其文档或采集记录。
- `KnowledgeObject`、`KnowledgeNode`、`KnowledgeAssertion`、`KnowledgeEvidence` 表示对象、层级属性、可审核的取值及其依据。原有实体关系与抽取批次也保留来源及审核记录。
- `Conversation` 和 `Message` 保存用户会话、回答状态和引用。`SiteConfig` 及 `ConfigPreset` 保存站点设置与分类预设。完整定义见 [kb/models.py](kb/models.py)。

维护命令集中在 [kb/management/commands/](kb/management/commands/)。变更历史见 [CHANGELOG.md](CHANGELOG.md)，上传恢复、知识采集和图谱等专题见 [docs/](docs/)。
