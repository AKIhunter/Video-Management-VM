# `logs/` — 运行日志（生成物）

## 里面是什么
`app.log` —— 应用运行日志，由 `app/logging_setup.py :: setup_logging()` 用
`TimedRotatingFileHandler` **按天滚动**，同时输出到控制台。

## 产生时机
`app/main.py` 的 `lifespan()` 在服务启动时调用 `setup_logging(cfg)`，
日志目录取自 `config.json` 的 `log_dir`。

## 删除的后果
**没有影响**，这是纯诊断输出。想清空直接删 `app.log*` 即可，服务运行中也可删（下次写入会重建）。

## 相关配置
| 想改… | 改这里 |
|---|---|
| 日志级别 | `config.json` 的 `log_level`（`DEBUG` / `INFO` / `WARNING` / `ERROR`） |
| 日志目录 | `config.json` 的 `log_dir` |
| 滚动周期 / 保留份数 / 日志格式 | `app/logging_setup.py :: setup_logging()` |
