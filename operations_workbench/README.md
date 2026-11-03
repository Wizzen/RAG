# 运维数据工作台

登录后打开 `/tools/operations/`。站点管理员及 Django「运维数据工作台」组成员可查看三类运维数据。访问组可在现有站点后台管理。

- Maximo 使用服务器端的只读 Oracle 连接查询工单和作业计划并生成中英文 PDF。
- Splunk 直接向已配置的 Splunk 服务器发出 Apex 日志查询。表单和 AI 均使用固定索引、时间窗口和结构化筛选。
- Downtime 支持读取导入快照或实时查询，可按事件时段继续查 Splunk。快照的截止日期显示在页面上。

首次导入或更新停机快照时，在项目根目录使用项目 Python 运行：

```sh
python -m operations_workbench.import_sources \
  --toolbox "/Users/sgao3/AI/SHDR Engineering Toolbox" \
  --apex "/Users/sgao3/AI/SHDL-Attraction-Apex-Portal"
```

本机运行资料存放在 `.runtime/operations-workbench/`，包含连接配置、JDBC 运行环境和停机数据库快照。若实际网页从其他工作副本启动，可将 `OPERATIONS_RUNTIME_DIR` 指向此私有目录。重新导入会从指定目录刷新配置和快照。源目录只在导入时读取。

AI 读取主站已选的本机回答模型，按白名单规划最多三次查询。回答附实际操作、参数、时间和结果。连接失败及快照覆盖不足会单独显示。实时访问需要 Oracle / Splunk 服务器在部署机网络中可达。
