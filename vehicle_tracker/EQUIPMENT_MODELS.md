# 设备模型接入

设备模型知识库入口为 `/tools/equipment-models/`。已有车辆三维工作台位于 `/tools/equipment-models/sdmt/`，原 `/tools/vehicle-tracker` 书签与项目仍可使用。

在目录中选择「新建设备」可建立通用设备档案。部件编号在单个模型内唯一，可以为每个部件填写上级、说明和图号，再登记学习笔记与追踪记录。点击图号可查询当前账号有权查看的资料，在本页定位原 PDF。个人设备保存在当前浏览器并按 RAG 账号区分。导出模型后，可在其他浏览器、账号或 RAG 实例导入。

## 版本 1 交换格式

保存为 UTF-8 JSON 文件，在目录选择「导入模型」。组件按固定编号引用上级，记录按固定编号关联部件。文件限 2 MB、2000 个部件及 4000 条记录，循环层级、无效图号、缺失关联或重复模型编号会被拒绝。设备侧系统也可生成同一格式。

```json
{
  "format": "shdr-equipment-model",
  "version": 1,
  "id": "demo-pump",
  "name": "示例泵组",
  "category": "泵组",
  "description": "接入格式示例",
  "components": [
    {"id": "P01", "name": "泵组总成"},
    {"id": "M01", "name": "驱动电机", "parent": "P01", "drawing": "DEMO-100", "description": "驱动泵组的部件"}
  ],
  "entries": [
    {"kind": "learning", "component": "M01", "at": "2026-01-01T08:00:00Z", "text": "记录已核对的部件资料。"}
  ]
}
```

记录类型支持 `learning`（学习笔记）和 `tracking`（追踪记录）。图号可省略，提供时使用 3 至 40 位英文字母、数字或短横线。

## 发布共享目录

将拟对所有已登录用户公开的模型清单放入 `DATA_DIR/equipment_models/*.json`。可用 Django 设置 `EQUIPMENT_MODEL_DIR` 指定独立目录。设备 ID 应唯一，`sdmt` 为内置车辆模型保留。个人笔记可在页面点击「创建学习副本」后添加。

登录后访问 `GET /tools/equipment-models/api/catalog/` 可读取版本化目录。内置三维查看器项返回同站页面地址，通用查看器项返回模型清单。文件只承载模型数据，页面根据内置查看器类型呈现内容。安装专用三维查看器时，可在注册表和 Django 路由中注册对应类型。

要给已发布的通用设备增加专用三维页面，先在本项目注册受登录保护的 Django 视图，然后按共享清单的设备 ID 绑定命名路由。例如以下部署设置给 `demo-pump` 增加「打开三维视图」入口，同时保留部件、笔记与追踪记录：

```python
EQUIPMENT_MODEL_VIEWERS = {
    "demo-pump": {"route": "pumps:viewer", "kwargs": {"pump_id": "demo-pump"}},
}
```

`pumps:viewer` 是设备接入方需要实现的视图。目录 API 在对应共享项的 `viewer_url` 字段返回本项目解析出的路径。未配置、路由无效或导入个人 JSON 时使用通用档案。
