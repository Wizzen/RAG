# 运维数据工作台

登录后打开 `/tools/operations/`。站点管理员及 Django「运维数据工作台」组成员可查看三类运维数据。访问组可在现有站点后台管理。

- Maximo 使用服务器端的只读 Oracle 连接查询工单和作业计划并生成中英文 PDF。
- Splunk 直接向已配置的 Splunk 服务器发出 Apex 日志查询。表单和 AI 均使用固定索引、时间窗口和结构化筛选。
- Downtime 支持读取导入快照或实时查询，可按事件时段继续查 Splunk。快照的截止日期显示在页面上。

设备模型知识库的 QAQC/MRB 记录也读取此处导入的 `qaqc.sqlite3` 快照，工单、作业计划和工单 PDF 使用同一 Maximo 连接。

首次导入或更新停机与 QAQC 快照时，在项目根目录使用项目 Python 运行：

```sh
python -m operations_workbench.import_sources \
  --toolbox "/Users/sgao3/AI/SHDR Engineering Toolbox" \
  --apex "/Users/sgao3/AI/SHDL-Attraction-Apex-Portal"
```

Splunk 服务器使用其出厂证书（不含主机名，签发 CA 为所有 Splunk 共用），系统 CA 无法校验。请在公司网络中固定服务器证书，并与 Splunk 管理员核对打印出的 SHA-256 指纹：

```sh
python -m operations_workbench.import_sources --pin-splunk-cert
```

固定后仅接受该证书，并在发送账号之前完成校验。重新导入会保留指纹。服务器更换证书后需重新执行。若已有签发证书的 CA 文件，可改为在连接配置中设置 `SPLUNK_CA_BUNDLE`。

本机运行资料存放在 `.runtime/operations-workbench/`，包含连接配置、JDBC 运行环境和停机数据库快照。若实际网页从其他工作副本启动，可将 `OPERATIONS_RUNTIME_DIR` 指向此私有目录。重新导入会从指定目录刷新配置和快照。源目录只在导入时读取。

AI 读取主站已选的本机回答模型，按白名单规划最多三次查询。回答附实际操作、参数、时间和结果。连接失败及快照覆盖不足会单独显示。实时访问需要 Oracle / Splunk 服务器在部署机网络中可达。

## 结果总数、分页与导出

列表查询（工单、作业计划、停机事件、停机统计、Splunk 日志）每次都会在服务器上对全部匹配记录统计精确总数和分组分布，页面显示「共 N 条，本批第 a–b 条」，可「读取上一批 / 下一批」。单批上限不变：工单 500、作业计划 200、停机事件 500、停机统计每页 100、Splunk 200。

AI 回答中的计数只引用服务器统计，模型只看到至多 8 条样本记录。回答下方注明总数、引用的样本数和可补充的筛选条件，「完整结果」可继续从服务器读取，页面内最多 2000 条。

「导出全部 CSV」调用 `POST /tools/operations/api/export/`，从第 1 条起最多导出 5000 行，并提示真实总数；超出部分请缩小日期或条件后分批导出。「导出本批」只导出当前页面已读取并过滤后的记录。导出仍为只读查询。

## 本地前端依赖

所有前端库都放在 `static/operations_workbench/vendor/`，不使用 CDN，`FOS_LOCAL_ONLY=1` 下可用。版本、npm 完整性校验值和许可证见 `vendor/ATTRIBUTION.txt`。

- PDF.js 4.10.38（含 `cmaps/` 与 `standard_fonts/`）：Maximo 报表使用不嵌入的 `STSong-Light` CID 字体，缺少 CMap 时只显示表格线。预览失败或无文本层时提供「在新窗口打开 / 下载原 PDF」。
- ECharts 5.6.0（`echarts.esm.min.mjs`）：只在 Downtime 显示统计时载入。图表基于服务器对全部匹配事件的统计；点击柱或扇区只筛选下方事件表，图表保持完整范围；载入失败时改为显示统计表。

升级库时替换整个带版本号的目录，更新 `ATTRIBUTION.txt` 中的校验值，并修改 `workbench.mjs` / `charts.mjs` 中的路径。页面资源版本号不统计 `vendor/`。
