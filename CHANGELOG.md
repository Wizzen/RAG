# Changelog

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
