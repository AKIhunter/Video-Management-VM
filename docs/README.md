# `docs/` — 项目文档

三份主文档各管一段，写作时**不要互相复制内容**，用链接引用。

```
docs/
├─ deployment.md    部署前置要求 + 部署流程（面向「把它跑起来」）
├─ user-manual.md   功能使用手册（面向「怎么用」）
├─ architecture.md  架构 & 数据模型 & 开发须知 & 阅读路线 & 未来 todo（面向「怎么改」）
└─ README.md        本文件（文档自身的组织与维护约定）
```

---

## 三份文档的边界

| 文档 | 回答的问题 | 不写什么 |
|---|---|---|
| [`deployment.md`](deployment.md) | 需要装什么？怎么装？ffmpeg 从哪来？为什么我的接口 404？ | 不写功能怎么用、不写代码结构 |
| [`user-manual.md`](user-manual.md) | 每个按钮是干嘛的？操作顺序是什么？有哪些约束？ | 不写安装步骤、不写实现细节 |
| [`architecture.md`](architecture.md) | 代码怎么分层？数据怎么流？想改 X 去哪改？新人按什么顺序读？ | 不重复部署步骤与操作手册 |

**代码级细节**（某个函数干嘛、某个常量改哪儿）写在**各代码目录自己的 README** 里：

[`app/`](../app/README.md) · [`app/core/`](../app/core/README.md) · [`app/services/`](../app/services/README.md) · [`app/routers/`](../app/routers/README.md) · [`webui/`](../webui/README.md) · [`tests/`](../tests/README.md)

---

## 一个功能改动的文档义务

| 改动类型 | 需要同步更新 |
|---|---|
| 新增 / 修改接口 | `architecture.md` 的接口与数据流；必要时对应 `routers/README.md` |
| 新增 / 修改配置项 | `deployment.md` 的配置表 + `config.example.json` + `app/README.md` 速查表 |
| 新增用户可见功能 | `user-manual.md` |
| 新增业务模块 | 对应目录 README + `architecture.md` 的分层图 |
| 改动界面 | `webui/README.md`（含可调常量位置） |

---

## 写作约定

- 中文书写，表格优先；命令用代码块并标注平台（PowerShell / bash）。
- 引用代码位置统一写成 **`文件 :: 函数`**（例如 `app/services/recommend.py :: _arrange`），方便直接搜索。
- 文档里出现的**本机绝对路径一律用示例路径**（如 `D:\MediaLibrary`），不要写开发者真实磁盘路径。
- 数值型描述（测试数量、文件行数）如与实际不符，**以实际为准**，发现过期就顺手改掉。
