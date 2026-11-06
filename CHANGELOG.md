# Changelog

## 2026-11-06 — 部署环境：网页采集浏览器与定时调度器

- 浏览器：本机此前只装了 `playwright==1.58.0` 而没装浏览器，`browser_open` 直接报 `Executable doesn't exist at ~/.cache/ms-playwright/chromium_headless_shell-1208`，网页模式采集、登录取数、动态 JSON 抓包全部不可用。已执行 `python -m playwright install chromium`，现在 `chromium-1208` 与 `chromium_headless_shell-1208`（Chromium 145.0.7632.6）就位，无需 `FOS_BROWSER_EXECUTABLE`。RHEL 8.10 上系统依赖已齐（`ldd` 无 `not found`，`install-deps` 不适用于本发行版，无需执行）。验证：`kb.tests.test_connector_browser` 26 项全通过（之前 13 项果环境报错），且用 cron 的最小环境（`env -i HOME=/root PATH=/usr/bin:/bin`）能启动、导航并抽取 DOM，所以调度进程里的网页重放也可用。占用 618 MB（在根分区，非 `/opt`）。
- 注意：`~/.cache/ms-playwright/chromium-1248` 与 `chromium_headless_shell-1248`（约 676 MB）是隔壁 `/opt/Mechanical_Site` 的 `playwright 1.64.1` 在用，不能删。
- 调度器：之前**没有任何调度进程**（无 `run_feed_scheduler`，root crontab 也没条目），已启用的数据源因此不会自动更新（天气源逾期两小时）。按文档采用系统 cron（`crond` 已 active+enabled），新增一行：`* * * * * cd /opt/FOS-RAG && /opt/FOS-RAG/.venv/bin/python manage.py sync_feeds --due >> /opt/FOS-RAG/logs/feeds.log 2>&1`（写入前已备份原 crontab 到 `/tmp/root.cron.bak.*`）。`sync_feeds` 也会刷新 `FeedSchedulerState` 心跳，数据源页的调度器状态仍为绿；重叠执行由数据库租约保证。实测：下一个分钟刻即补跑逾期数据源（`trigger=cron`、completed、1 条入库）并把 `next_run_at` 推回整点。
- 未覆盖：Web 进程本身仍是手动 `nohup` 启动，重启主机后不会自拉起（调度器会）。需要的话可以给 `runserver` 与 `run_feed_scheduler` 各建一个 systemd 单元。

## 2026-11-06 — 定时采集任务抽成通用服务层（页面/命令行共用）

- 背景：「一句话→定时采集任务」的全部规则先前写在 HTTP 视图里（`connector_views.create/refine/enable`），没有进程内函数也没有机器可用的入口；本次先把服务层和命令行做起来（REST 与对话内工具待做）。
- 新增 `kb/collector_api.py`：`require_manager`、`resolve_private`（内网开关单一判定：缺省沿用上一轮，关→开仅限全局管理员，随时可关）、`build_credential`/`resolve_auth`（结构化认证落库加密，或复用页面已存凭据）、`create_draft`、`refine_draft`、`approve`、`cancel`、`status`、`source_url`。只收普通参数、用 `ValueError` 报错，不接触 `request`/`JsonResponse`。
- 页面变薄壳：`connector_views.create/refine/enable/cancel` 只保留 HTTP 解参（页面的「复用已存凭据」仍由 `_credentials` 解析）后转调服务层；`enable` 那 60 行建库/部门/cron/凭据搬运/CAS 状态机/首跑全部迁到 `approve`，行为逐字对齐。新增迁移 `0044_connectordraft_created_via`（web/chat/api/cli 审计）。测试的 `start_async`/`start_feed` 补丁点随之改到 `kb.collector_api.*`（否则补丁失效会真开线程）。
- 命令行：新增 `manage.py build_feed`。`--url/--token-env/--header-env/--kb/--private/--wait/--approve/--no-first-run/--write-mode/--job/--json`；密钥只从环境变量读，不进命令行历史、任务标题与活动日志；默认不批准，`--approve` 才建 cron 数据源并首跑；退出码 0 就绪/已启用、1 失败、2 需补充信息。
- 凭据静默失败修复（之前导致“API key 不对”的误判）：`kb/feed_credentials.py` 新增 `normalize_auth`——缺 `type` 时只推断形状明确的（`token`→bearer、`username+password`→basic、`headers`→headers），歧义或缺 `type` 一律报错并列出可接受形状，绝不静默当「不认证」（旧行为下 `store()` 返回 None 但工具仍回 `saved:True`，凭据从未存上，后续每次请求都 401）。`set_source_access` 现在返回 `credentials_attached` 与补救提示，并在替换凭据时清理旧密文；工具文档写明必须带 `type` 的形状。
- 建任务时先落库凭据再脱敏需求文本，用户粘的密钥在服务层就被换成 `[REDACTED]`，模型改用 `request_headers()` 取用，不会拹进 URL。
- 测试：新增 `kb/tests/test_collector_api.py`（18 项：认证形状推断/拒绝、结构化 bearer 入库+脱敏+入口标记、内网开关权限、部门管理员可用页面凭据、批准幂等与未校验拒绝、网页未完整验证拒绝、refine 延续凭据/开关/入口、build_feed 三条路径）；`kb/tests/test_connector_chat_regressions.py` 新增两项（缺 `type` 不假报 saved、`type=none` 必须告知未绑定凭据）。相关套件 189 项通过（仅 `test_connector_browser` 13 项因本机未装 Chromium 报错，与本次无关）。

## 2026-11-06 — 回答输出预算提到 16K（问答与分析报告）

- 现象：AI 助手长回答（表格、多引用）写到一半停住，界面报「模型输出被截断，回答未完成」（`kb/agent.py:1412-1416`，`finish_reason=length`）。预算来自 `fos_rag/settings.py:21` 的 `LLM_MAX_OUTPUT_TOKENS`，默认 **2048**，站点配置页没有这个字段（`SiteConfig` 只有 Base URL/Key/模型/温度/远程开关）；当前回答模型 `swift-1.5-iq3_xxs` 是思考型模型，思维链也计入输出预算，2048 里大半被思考吃掉。实测该服务接受 `max_tokens` 到 32768。
- 配置：`.env` 写入 `LLM_MAX_OUTPUT_TOKENS=16384`，同时把 `QA_TURN_TIMEOUT` 提到 600 秒（`kb/views.py:1313` 用它包住整轮问答，16K 输出加多轮工具会先撞上原来的 240 秒）。`.env.example` 与 `docs/quality-upgrade.md` 同步说明，默认值仍为 2048，不改代码的部署行为不变。
- 分析报告：`fos_rag/isolated_model.py` 原先把隔离分析进程的 `max_tokens` 硬夹在 256–2048，光调环境变量对它无效；上限提到 32768（保留边界，防止异常取值把子进程挂死）。子进程 `timeout` 55→165 秒、worker 内 `httpx` 读超时 45→150 秒（先于子进程结束以便返回可读错误），stdout 上限 64000→160000 字节，答案字符上限 16000→24000（两处保持一致）——否则 16K 预算只意味着「一定超时」。
- 核实器：`kb/qa_steps.py` 的 `_VERIFY_MAX_TOKENS` 2500→**8000**。光改这一个数不会生效：`build_llm` 自己的 `ChatOpenAI(timeout=45)` 与 `verify_answer` 的 `asyncio.wait_for(..., 45)` 会先在 45 秒掉断，而 `verify_answer` 的任何异常都 `return None` → `kb/agent.py:1508` 直接**拒绝发布草稿**（比 `finish_reason=length` 更隐蔽）。现在 `build_llm(llm_cfg, timeout=…)` 可按步骤传超时：核实用 `_VERIFY_HTTP_TIMEOUT=140`（HTTP 层）+ `_VERIFY_TIMEOUT=150`（asyncio 层，略宽，让网络错误先带回可读原因），问题规划仍用默认 45 秒（它的 `wait_for` 仍是 25 秒）。实测核实器输出本身就是小 JSON（15015 字长回答只用 763 tokens，16k 输入只用 188 tokens），所以 8000 是安全余量而不是新瓶颈；`max_tokens` 是上限不是目标，不会因此变慢。长回答真正碰到的限制是 `verify_answer` 开头的 `len(answer) > 16000` 字闸门（实测 15900 字正常核实、16100 字直接不调用模型并阻止发布）。
- 不变：图谱抽取仍用自己的 32768（`kb/graph_extraction.py:26`）、AI 采集助手 8192（`kb/connector_agent.py:443`）、检索计划 1000（`kb/qa_steps.py:23`）、站点配置「测试」64；需重启 `runserver` 才生效（`--noreload`，settings 只在导入时读一次）。

## 2026-11-06 — AI 采集助手可用内网接口（仅全局管理员）

- 症状：让 AI 采集助手读取内网接口（例如 `http://10.134.30.57:8000/api/v1/docs/`）时，`read_api_documentation` 与 `probe_api` 连着报「API 指向受限网络地址；内网服务需由全局管理员明确启用」，任务停在 `needs_info`，还把这句话当成问题丢回对话。原因不是缺白名单：`kb/feed_http.py` 的 SSRF 防护按**单个任务**的 `allow_private` 放行内网地址，而 AI 对话页只提交 `{prompt}`／`{feedback}`，从不带这个开关，于是即便账号是超级管理员，草稿里也永远是 `allow_private=False`；唯一的勾选框只在数据源表单上，那条路径只写 `KnowledgeFeed`，碰不到 AI 草稿。
- 页面：`templates/kb/connector.html` 在输入框上方加「内网配置（仅全局管理员）」折叠面板与「允许访问内网或本机 API」勾选框（`index` 已下发 `is_global_admin`，非管理员不渲染）。`static/js/connector.js` 发送时透传 `allow_private`，并在打开对话／新建任务时与任务状态同步；若消息里出现 10.x、192.168.x、172.16-31.x、127.0.0.1 之类的地址而开关未开，直接给出可操作的提示并展开面板，不再空跑五轮工具。资源版本号升到 `connector.js?v=12`／`connector_chat.css?v=7`。
- 服务器：`kb/connector_views.py` 的 `refine` 接受 `allow_private`，默认沿用上一轮（编辑内网数据源的对话照常继续），把开关从关改开仍需全局管理员，随时可以关掉。凭据、批准后写入 `KnowledgeFeed` 与后续 cron 的传递链沿用原逻辑，`kb/feed_http.py` 的防护一行未改：链路本地／元数据、组播与未指定地址仍一律拒绝，DNS 仍逐次校验并固定，TLS 主机名校验保留。
- 测试：`kb/tests/test_connector.py` 新增用例覆盖开关仅全局管理员可开、随对话延续、可随时关闭，以及页面对部门管理员不渲染该开关。

## 2026-11-06 — PDF 解析支持 MinerU 4.1.x（Tasks API）

- 协议适配：`kb/pipeline.py` 按 `/v1/health`（其次 `/v1/models` 的 `mineru-<version>` 虚拟模型）报告的版本号自动选择解析协议，结果缓存在本机 5 分钟。4.0.x 仍走原 File API（`/v1/file_parse` + `/v1/uploads` 三步 + `/v1/tasks/{id}/result`）；4.1.x 改走 Tasks API：`POST /v1/tasks` 声明文件（`output_formats:["default"]`）→ `PUT` 响应给出的上传链接传字节 → `GET /v1/tasks/{id}` 轮询 → 下载 `files[].result_url` 指向的 `result.zip`，取其中的 `markdown.md` 与 `structured_content.json`。4.1 删掉了 inline 通道与 `/v1/uploads*`，`output_formats` 也不再接受 `markdown`/`structured_content`，因此必须在传输层分叉。
- 产物归一：两种协议的结果统一交给 `_mineru_finalize`。插图既支持 4.0 的内联 data URL，也支持 4.1 结果包里的 `images/*` 成员，一律改写为 `images/<内容哈希>.<扩展名>` 并登记；`structured_content` 的 pages→blocks 仍换算为 0-1000 bbox，chunk 溯源与 PDF 红圈定位不受影响，下游无需改动。
- 档位与租约：4.1 的请求档位对照 `/v1/tiers` 发布的能力，配置里写死的档位不可用时按 standard→basic→flash→advanced 退让；建任务、上传与查询均带 `X-MinerU-Task-Lease-Seconds: 3600`，避免大文件解析或慢下载期间结果被服务端回收。
- 设置页：修好 MinerU「测试」。旧探测请求的是根 `/health`，而 `mineru-api` 从 4.0 起只有 `/v1/health`，真机上一律报「健康端点异常」；现在读 `/v1/health`（其次 `/v1/models`）的版本号并回报将要使用的协议（例如「MinerU 4.1.7 可达，PDF 将按 Tasks API（4.1） 解析」），鉴权失败会提示补 API Key；只响应根 `/health` 的 3.x 服务直接判为不支持并给出升级提示，读不到版本号则算连通但给出提醒。编辑框补上档位取值与「语言已废弃」说明，配置卡片改显示「自动识别 4.0 / 4.1」。`start_all.bat` 里残留的 `/health` 提示地址一并修正（它等待的一直是正确的 `/v1/health`）。
- 依赖：`requirements.txt` 升到 `mineru[torch]==4.1.7`；`pyproject.toml` 里遗留的 `mineru==3.4.5`（3.x 的表单接口在 4.0 就被移除）同步修正。解析服务仍可用 `mineru-api` 单独部署在其他机器，本应用不依赖它的 Python 包。
- 测试：新增 `kb/tests/test_mineru_protocol.py`（`httpx.MockTransport` 全离线）覆盖版本探测与缓存、未来版本按 Tasks API 处理、4.1 完整流程（声明→上传→轮询→下载 zip、任务失败、坏包、缺 `result_url`、档位退让、旧 backend 映射）、4.0 的 inline 与大文件 uploads 三步、3.x 拒绝与探针报告。`kb/tests/test_local_ai_config.py` 的 OCR 测试改为断言协议探测结果。

## 2026-11-04 — 运维工作台：Downtime 统计图表与联动

- 图表库：本地放置 Apache ECharts 5.6.0（npm `echarts@5.6.0` 官方 `dist/echarts.esm.min.mjs`，完整性校验通过，SHA-256 记在 `vendor/ATTRIBUTION.txt`），只在 Downtime 结果含统计时按需载入；不使用 CDN。
- 图表（全部基于服务器对全部匹配事件的统计，而非当前页）：每日趋势（事件数柱 + 停机分钟折线，双纵轴，超过 31 天时可缩放）、景点 Top 10 横向条形、停机代码环形图、分类 Top 10、设备（资产）Top 10（按停机分钟，注明涉及资产总数）、日期 × 景点热力图（前 15 个景点）。悬停显示数值；每张图可「下载 PNG」。
- 联动：点击日期柱、景点、停机代码扇区、分类或设备，只重新查询下方事件表（服务器 `drill_kind` / `drill_key`，白名单为日期、景点、分类、停机代码、资产），图表保持完整范围不重绘；显示「已筛选 … · 共 N 条事件」及「清除筛选」。筛选后仍可分批读取和导出。事件行保留「同段日志」联查 Splunk。
- 统计卡片：事件数、停机分钟、平均每次分钟、尚未恢复，带图标，并与上一等长周期对比（▲ 增加以警示色、▼ 减少以绿色显示，悬停可见上期日期）。快照未覆盖上一周期或处于筛选状态时不显示对比。
- 主题与窄屏：颜色取自 `fos.css` 主题变量，切换亮暗主题时自动重绘；容器尺寸变化时自适应；760px 以下单列，环形图在窄卡片中隐藏扇区标签、保留图例。原统计表保留在「查看统计数据表」中；图表组件载入失败时自动改为显示统计表。
- 测试：新增 `operations_workbench/tests/test_charts.py`（ECharts 文件校验值与许可证、仅加载本地代码、按版本懒加载、图表下钻种类均被服务器接受、筛选不改变图表范围）；`test_totals.py` 增加「每个图表分组下钻后的事件数等于该分组的数值」「热力图合计等于总数」与上一周期窗口计算。`operations_workbench` 55 项测试通过。

## 2026-11-04 — 运维工作台：工单任务卡片与状态履历时间线

- 任务步骤由逐字段列表改为卡片：每个任务一张，按任务号数值排序；头部为「Task 20」徽标、中文标题、状态徽标和工期，右上角小字显示子工单号与任务 ID；展开后先显示中文详述，「显示英文」切换英文标题与详述，并显示实际完成时间。
- 工具栏：搜索（任务号、中英文标题与详述、子工单号、任务 ID、状态），显示「显示 n / 总数 项」；「全部展开 / 全部收起」；「紧凑表格」切换为任务号、中文标题、状态、工期、子工单、实际完成六列表格。作业计划详情的任务步骤使用同一组件。卡片正文在首次展开时才生成。
- 状态履历改为竖向时间线，最新在前，当前状态高亮；每条显示状态徽标、变更时间、在该状态停留时长、变更人与备注。停机详情中的资产运行状态事件字段不同，仍按原列表显示。
- 测试：`test_detail_view.py` 覆盖排序、搜索、展开/收起、紧凑表格、英文切换与最新在前排序的实现约束。

## 2026-11-04 — 运维工作台：工单详情重设计

- 顶部固定信息栏：工单号、状态徽标（中文状态名，原代码在提示中），副标题为「景点中英文名 · 工单类型 · 班组」，右侧「中文 PDF / 英文 PDF / 查看作业计划」。对话框改为正文区滚动，信息栏在对话框与 AI 侧栏中都保持在顶部。
- 关键信息网格：工单号、工单 ID、作业计划（附说明，可点击打开）、资产（附资产说明）、优先级、父工单（可点击打开）、报告人、故障代码。
- 分组（可折叠）：描述（中英文并排，含详述和作业计划说明）、计划时间（按时间排序的横向时间线，实际时间以绿色标记，未设置的时间点单独列出）、工时（预计工期、预计人工工时、扳手工时）、其他字段（默认收起）。
- 字段中文化：补全工单详情 SQL 全部 43 个输出列的中文名（如 `location_description` → 位置说明、`estdur` → 预计工期）；`taskid` / `jobtaskid` 分别为「任务号」「任务 ID」，不再重名。
- 格式：工时显示为 `23.98 h`；时间统一为 `YYYY-MM-DD HH:mm`（带时区的值换算到上海时间，Splunk 日志时间保留秒和毫秒）；空值显示「—」。`targstartdate` / `targcompdate` / `pmduedate`（UTC 原值）与对应 `*_local` 去重，只显示现场时间，悬停可见 UTC 原值；作业计划等其他详情同样适用。
- 样式在新文件 `static/operations_workbench/detail.css`，只使用 `fos.css` 主题变量，亮色和暗色主题均可用；≥320px 宽度无横向溢出（窄屏时关键信息两列、描述上下排列、时间线改为竖向）。
- 前端子模块改为按页面资源版本号动态加载（`import('./x.mjs' + version)`），避免浏览器缓存旧子模块与新入口脚本混用导致页面无法加载。
- 测试：新增 `operations_workbench/tests/test_detail_view.py`（详情、任务、状态 SQL 的每个输出列都有中文名，UTC/现场时间配对存在于 SQL，工时与日期格式化规则，子模块带版本加载，样式只用主题变量，页面引用 `detail.css`）。

## 2026-11-04 — 运维工作台：精确总数、服务端统计、分批读取与全部导出

- 现象：运维 AI 查询证据显示 `truncated: true`，回答只能基于前几条记录，统计类问题（如「按状态统计」）给出的是样本数而不是真实数量。
- 各层上限（修改前）：Maximo 工单 `ROWNUM` 默认 50 / 最多 500，作业计划 50 / 200，作业计划关联工单 50 / 100；停机事件快照 50 / 500，实时 500；停机总览 25 / 100 每页；Splunk 50 / 200；`ai.py` 把每个列表截到 8 条、证据 JSON 硬截到 8500 字符（可能截断在字段中间）；前端每页 20 条、单元格 240 字，CSV 只导出当前批次。
- 精确总数与全量统计：Maximo 工单 / 作业计划 / 作业计划关联工单每次查询并行执行同条件的 `GROUPING SETS` 汇总，返回 `total` 以及按状态、类型、班组、景点、日期（作业计划为状态、工艺、频率）的分布；分页改为 `ROWNUM` 区间加 `offset`。停机事件返回总数、全量汇总、按资产 Top 15、日期×景点热力数据和上一周期对比；Splunk 每次另跑 `stats count by attrname` 和 `top apexmssgmnemonic`，总数不小于已返回行数。`truncated` 现在表示「总数大于本批返回数」。
- 运维 AI：解释模型只看到 `result_summary.total_matches`（精确总数）、`server_aggregates`（服务端统计）和至多 8 条 `sample_rows`；超出约 9000 字符时减少样本和分组项，不再截断 JSON。提示词要求「共 N 条」和计数只引用服务端数字、样本不得用于推算。回答下方显示由服务器生成的覆盖说明（共 N 条、回答引用前几条样本、可补充的筛选条件），并可展开「完整结果」表格，从服务器继续读取下一批（页面最多读取 2000 条）。
- 导出：新增 `POST /tools/operations/api/export/`，仅支持列表类查询，从第 0 条读取至多 5000 行并返回真实总数；超出时提示已导出前 5000 条及缩小范围。工具栏分为「导出本批」（应用页面内过滤）和「导出全部 CSV」，AI 完整结果中也可导出。全部只读，仍经过隔离 worker 和字段目录校验。
- 结果面板：状态与表头显示「共 N 条，本批第 a–b 条」；Maximo 工单 / 作业计划和停机事件与 Splunk 一样支持「读取上一批 / 下一批」。
- 测试：新增 `operations_workbench/tests/test_totals.py`（汇总 SQL 与分页绑定、恰好等于上限不算截断、导出上限只放开给导出、快照总数/热力/资产/上期对比/图表下钻、实时停机总数、AI 证据摘要与覆盖说明、导出接口上限与权限）；更新 Splunk 测试以覆盖三次并行检索。`operations_workbench` 39 项测试通过。

## 2026-11-04 — 运维工作台：修复 Maximo 报表 PDF 只显示横线

- 现象：运维 AI 侧栏和 Maximo 详情里打开 Job Plan / 工单 PDF，只显示页数和表格横线，中英文都没有文字。两处入口共用 `showPdf()`，行为一致。
- 根因：`maximo_pdf.py` 全部文字使用 ReportLab 的 `STSong-Light` CID 字体（`UniGB-UCS2-H` 编码，不嵌入字体）；页面内 PDF.js 未配置 `cMapUrl` / `cMapPacked` / `standardFontDataUrl`，且本地只有 `pdf.min.mjs` 和 worker，没有 CMap 与标准字体，无法解码文字。后端返回完整字节（非流式、`%PDF` 到 `%%EOF`），CSP 未拦截。同版本 PDF.js 对同一文件对比验证：不配 CMap 时文本层为空，配本地 CMap 后中英文文字均正常。
- 修复：在 `static/operations_workbench/vendor/pdfjs-4.10.38/` 放置官方 `pdfjs-dist@4.10.38`（npm 完整性校验通过，`pdf.min.mjs` 与原副本哈希一致）的库、worker、`cmaps/` 和 `standard_fonts/`；以绝对 URL 配置 CMap 与字体（相对路径会按 worker 位置解析成 404）。不再引用 `vehicle_tracker` 的副本，无外部 CDN。
- 兜底：预览组件载入失败、渲染失败或当前页无文本层时，显示「在新窗口打开」「下载原 PDF」。PDF 接口新增 `?download=1`（附件下载，文件名仅保留安全字符），并显式返回 `Content-Length`。
- 页面资源版本号不再哈希 `vendor/` 目录（目录名已带版本），避免每次打开页面多读约 4 MB。
- 测试：新增 `operations_workbench/tests/test_pdf.py`，覆盖接口完整字节与长度、下载模式、报表实际使用的每个 CMap 均在本地 static、前端配置路径与版本一致。

## 2026-10-01 — PR 集成验证与升级说明

- 将远程 API 配置入口/总开关、上传恢复与表格兼容、手册概览修复集成到现有 PR #3，保留已提交的后台回答、设备隔离、未读提醒和响应式页面改进。
- 在最终 PR 分支重新执行：206 项 Django 测试、23 项 Node 测试全部通过；`makemigrations --check --dry-run` 无遗漏，diff 检查通过。
- 从尚未启用全部远程 API 开关的版本升级，需要执行 `python manage.py migrate` 应用迁移 0028；开关默认关闭。上传和概览修复本身不增加数据库迁移，也不自动重解析已有资料。
- 升级前备份业务数据库和索引，等待当前上传/回答结束后重启 ASGI。显式重建命令需暂停入库；同主机队列锁不支持跨服务器调度。
- 回退代码前先停止 Web 并备份数据；旧代码可读新索引，但无法提供批次续传。远程 API 总开关迁移保留兼容字段，可先保留数据库再回退代码；本轮不删除资料或已有索引。

## 2026-10-01 — 修复手册概览串库与章节遗漏

- 根因：混合文件名 `FL-8B漂流 使用说明书 PRINT.pdf` 的中文设备名没有独立参与词面路由；无明确范围时可能落到另一份默认手册。新增从文件名提取中文主题、剥离通用手册后缀的匹配，无需维护设备别名词表；同分不同资料仍视为歧义。
- “漂流手册有啥，给我总结一下”等整本概览先从本地关键词索引读取目录和各章代表片段，再生成回答，避免相似度 Top-K 只覆盖局部。兼容旧索引去掉章节数字、截短标题的情况；目录展示使用保存的原始 Markdown 标题。
- 概览片段有数量及字符预算，保留完整片段、不截断条件；说明目录和代表片段并非全文。来源快照、红圈出处与发布时的权限/内容校验继续使用现有机制。
- 概览聚焦章节内容，不罗列 OCR 数字和设备参数；强制先核对草稿再发布，即使普通解释的增强开关关闭也不提前发布。文件夹内多本手册要求先明确文档，避免混成一本。
- 无数据库迁移、无需重解析或重建索引。回滚本节对应代码即可；旧错误回答保留为历史记录，修复后的回答需要重新提问生成。
- 核对不通过时不直接拒绝整本概览：程序从当前可见文档、校验值一致的原始解析文本中摘取章节标题，作为明确标注的原文章节概览；不复用被拒绝草稿、不补写数字。发布前仍再次核查来源权限和内容。
- 验证：206 项 Django 回归通过，覆盖原问及改写、混合中文文件名、同名主题歧义、后部章节、旧标题形式、完整片段预算、强制核对及拒绝草稿后的原文回退。原问题真实回放约 56.40 秒，核对/原文发布检查通过，仅引用 `FL-8B漂流 使用说明书 PRINT.pdf`；小矮人等范围外出处 0。回放中模型的扩写草稿仍有缺证据条款，已回退为原文章节概览；不宣称所有自由总结事实问题已解决。已在无活动回答和上传任务时重启本地 Web。

## 2026-10-01 — 上传排队、分批保存和失败续传

- 上传流程改为同一主机串行队列：数据库保存 pending 状态，主机文件锁防止多个 Web 工作线程同时解析；ASGI 重启后自动回收并继续中断任务。
- 向量片段使用稳定 ID 分批 upsert，保存成功批次作为检查点。重试跳过已存在片段，复用已保存 OCR，不再整份重新计算。
- 超时、429、5xx 仅对失败批次进行一次拆分重试；认证、维度等错误直接失败。半批仍失败则停止并保留进度，不无限重试。
- 管理页显示批次进度、增加「重试（继续进度）」按钮；重复重试返回 409，保留部门权限和 CSRF。未完整写入关键词索引的任务不标记完成。
- 新集合保存向量配置指纹，阻止模型/端点变化后混用片段；无需新增数据库迁移。
- 验证：196 项 Django、23 项 Node 回归通过；包括真实 Chroma 中断后续传、两个工作线程串行处理和关键词失败恢复。运行边界及回退见 [上传可靠性说明](docs/upload-reliability.md)。

## 2026-09-30 — 修复表格省略结束标签导致整份文档入库失败

- 现象：「测试用」库中 `FL-8B漂流 使用说明书 PRINT.pdf` OCR 成功，向量化前报「表格 HTML 不完整」，整份文档失败。
- 根因：MinerU 输出的 79 张表中有 2 张电气接线表，最后一行缺少 `</tr>`，直接以 `</table>` 结束。HTML 规范允许省略 `</tr>`、`</td>`、`</th>`，由下一行、下一单元格或 `</table>` 隐式闭合；`kb/table_structure.py` 原先将其视为残缺。
- 修复：解析器按 HTML 规则隐式闭合单元格与行，末行内容完整保留。缺少 `</table>` 的截断表格和嵌套表格仍然报错，不静默丢内容。该文档 79 张表现已全部可解析，两张表的末行均已进入嵌入文本。
- 新增 `python manage.py reindex_failed [--doc <id>] [--dry-run]`：用已保存的 OCR 结果恢复失败文档，不重跑 MinerU。只处理独占文档库的失败文档；所在库还有其它文档时跳过；先检测 embedding 端点，不可用则不修改任何数据。
- 验证：新增 5 项回归（省略结束标签、截断/嵌套仍拒绝、恢复命令成功/共享库跳过/dry-run），共 179 项 Django 回归通过。真实文档 dry-run 列出 1 份待恢复；实际恢复因 8081 WeMM 向量服务未运行而安全中止，该文档仍为失败状态，待向量服务启动后执行。
- 网页进程以 `--noreload` 运行，需重启后新上传才使用修复后的解析器。

## 2026-09-30 — 修复索引数据库只读错误 1032

- 重建与删除知识库通过 Chroma API 删除集合，不再直接删除正在使用的 SQLite 目录，避免旧连接触发 `SQLITE_READONLY_DBMOVED`。删除并重建集合仍支持更换向量维度。
- 统一刷新本进程集合缓存，保留其他集合和数据库文件；只忽略集合不存在，不吞掉真实存储错误。
- 回归使用真实 Chroma 客户端，验证持有连接时重建、维度变化、保留其他集合、数据库 inode 不变及重复清理。
- 验证：180 项 Django 回归通过。受影响文档从已保存 OCR 恢复为 completed，数据库片段数与实际 Chroma 向量数均为 269，错误信息已清除；已备份业务数据库并重启 Web 刷新缓存。

## 2026-09-30 — 修复 WeMM 入库请求超时

- WeMM 的 OpenAI 兼容接口原先每批 16 条、等待 45 秒，资源竞争时一批耗时可能超过上限。改为默认最多 4 条、等待 180 秒，原生接口同步使用相同配置。
- 增加 `WEMM_BATCH_SIZE` / `WEMM_REQUEST_TIMEOUT` 部署参数；其他向量模型保留原批次及超时规则。
- 回归通过 MockTransport 验证 9 条输入实际分成 4/4/1 请求，以及原生接口批次、顺序、向量数量和超时配置。失败资料从保存的 OCR 恢复，不重新解析。

验证：182 项 Django 全量回归通过，diff 检查通过。失败资料以新批次完成真实向量化恢复，文档状态为 completed、数据库片段数与实际 Chroma 向量数均为 269、错误信息清空。已重启 Web 刷新集合缓存。

## 2026-09-26 — 配置首页直接切换远程 API

- 在 LLM 模型和地址下方直接显示远程／局域网 API 开关、已开启／已关闭状态及资料发送说明，无需打开编辑弹窗。
- 点击立即保存并刷新状态，保留地址、密钥、模型和其他服务配置；当前配置标为自定义，不覆盖已保存预设。保留管理员权限和 CSRF 校验。
- 增加开关往返保存、配置保留、预设不变、非法值及非管理员访问回归。

## 2026-09-26 — 全部远程 API 总开关

- 配置页顶部新增「允许全部远程／局域网 API」，覆盖 LLM、向量、重排和 MinerU，支持 HTTP/HTTPS，点击即保存。默认关闭，不修改服务地址、密钥或预设。
- 离线边界改为同时维护四类已配置端点的主机/端口白名单；任意服务读取配置均同步全部端点，避免服务间互相覆盖放行状态。非配置地址保持禁止。
- 总开关开启时，LLM 独立按钮显示由总开关控制；关闭总开关恢复 LLM 原独立设置，其他服务恢复离线模式的本机限制。
- 新增迁移 0028；数据库已备份并完成迁移，页面重启后确认总开关可见。174 项 Django 回归、迁移一致性及 diff 检查通过，未进行真实远程服务端到端验收。

## 2026-09-22 — 远程与局域网回答 API

- 回答模型新增「允许远程回答 API」开关，支持 OpenAI 兼容服务；HTTP 与 HTTPS 均可使用，局域网 IP 与公网域名适用相同选择规则。
- 非本机调用仍需显式开启；离线模式仅放行配置的主机、端口及 DNS 解析地址。旧配置默认关闭，旧预设不会继承远程权限。
- 测试和实际问答共用配置校验；远程模式不发送本地引擎参数或可选流式 usage，避免服务兼容性错误。不跟随重定向、不使用系统代理。
- 增加迁移 0027；保留现有本地模型配置。页面说明问题、历史和检索资料的发送范围，向量、OCR、重排仍独立配置。
- 验证：170 项 Django、23 项 Node 回归通过，迁移一致性与脚本语法检查通过。未配置真实外部账号，未宣称远程或局域网服务实测通过。

使用与回退见 [远程回答 API](docs/remote-answer-api.md)。

## Unreleased — 后台回答、设备范围隔离与界面改进

### 回答任务与会话恢复

- 回答生成从 HTTP/SSE 连接中独立出来。切换页面、刷新或断开显示连接后继续生成；回到会话自动恢复正文、引用和进度。只有显式停止、删除会话、超时或服务关闭才终止任务。
- 增加排队和生成中状态；同一会话只允许一个活动回答，当前 Web 进程一次执行一个生成任务，其余排队。重复提交返回 409。
- 首页复用公共数据流客户端，处理断流、超时、错误和完成事件；恢复读取忽略过期响应，权限失效或会话删除时停止轮询。
- 保存首字前失败原因，区分没有正文与已有部分正文。服务重启遗留的活动状态改为未完成，不让页面永久转圈；不承诺模型推理断点续算。

### 检索范围与回答输出

- 设备文件夹参与名称路由；同范围内中英文资料可共同检索。用户明确指定资料时覆盖旧默认库，切换主题时清除旧上下文，后续追问沿用最近明确命名的范围。
- 目录、关键词／向量检索、文件夹展开、原文提取和台账工具共同执行后端范围白名单，模型不能扩大检索范围。
- 工具调用最多五轮，重复同工具同参数时提前收尾，依据已有原文回答或说明缺失，避免循环耗尽步数后没有正文。
- 按完整段落发布普通回答，拦截拆分传输的原始工具调用标记；增强模式仍须通过来源与语义核对，不提前发布草稿。

### 提示与布局

- 生成中的会话标题缓慢闪烁，支持系统减少动画偏好。成功回答完成后显示未读小点，点击会话后消失；已读状态持久化，旧点击不能误清掉新回答提醒。
- 检索页最大宽度调整为 1680px，移除聊天栏的额外限宽；侧栏在 240–280px 自适应，标题增至 14px，两侧保留适度空白。保留手机抽屉和宽表格内部滚动。

### 迁移、部署与回滚

- 运行 `python manage.py migrate`，应用 0024–0026：未完成原因、活动状态与单会话唯一约束、已读游标。既有完整回答初始化为已读，不批量弹出旧消息提醒。
- 升级前等待当前回答结束并备份 SQLite；升级后重启 Web、刷新浏览器。无模型替换或文档索引重建。
- **后台任务目前仅支持单 Web 进程 ASGI 部署**。多 worker／多机需要共享任务队列；进程重启保留已保存内容，但不自动续算。
- 回滚前停止 Web，将 queued/running 记录标记为 incomplete；使用本版本代码回迁到 0023，再恢复旧代码。回迁会丢弃失败原因与已读游标，保留消息正文。详见 [后台任务运行说明](docs/background-answers.md)。

### 验证与边界

- Django 全量回归 162 项、Node 输入／数据流／恢复／通知回归 23 项通过；包含断开后继续、主动停止、排队、权限隔离、范围拒绝、追问继承与未读确认。
- 本地真实连接在首个心跳后断开，后台仍完整保存答案；浏览器验证切换页面再返回、进度恢复和未读小点点击清除。
- 真实指定设备问题回放只引用限定范围内的两份语言版本，范围外引用和工具标记均为零；这不等同于全部事实已完成人工核验。
- 1920px 屏幕下主体 1680px、侧栏 280px、回答栏 1276px；390px 屏幕下回答栏 358px，页面无横向溢出。
- 未宣称代表性资料集准确率或生产延迟目标通过。部分实测中重排服务未启动，使用原有降级检索。

---

## Previous release — PR #2

- Preserve HTML table cells when parser output omits span attributes, quotes them, reorders them, or uses header cells; reject malformed spans instead of silently dropping content.
- Bound embedding batches and request timeouts; support OpenAI-compatible WeMM endpoints and complete reranker URLs.
- Validate model test responses, distinguish inference from index compatibility, and provide a sequential test-all action. MinerU HTTP 200 health responses count as successful health checks.
- Bound conversation history and whole-turn execution; add streaming heartbeats, cancellation, timeout handling, and explicit incomplete-answer errors. Persist complete answers before sending the terminal SSE event.
- Improve citation navigation, page validation, Chinese IME handling, and add a return-to-site link in Django admin.
- Add optional structured query planning and verification-before-publication with current-source checks; keep enhancement opt-in.
- Correct evaluation ranks to use the globally merged retrieval order.
- Add opt-in local resource restrictions and configurable locally hosted Pyodide assets.

See [upgrade notes](docs/quality-upgrade.md) for configuration, migration, validation and limitations.

### Conversation persistence follow-up

Preserve interrupted visible answers with an incomplete status when switching pages; keep them out of future factual context. Remove device-name examples from the system prompt and prevent unverified historical claims from being replayed. Requires migration 0023. Regression totals: 143 Django and 15 Node.
