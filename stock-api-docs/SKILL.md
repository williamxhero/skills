---
name: Stock API Docs
description: 当需要查询 MarketHub（中文名：合源）这套 API 的用法时，先读取本地文档服务的根入口文档，并按 `/docs` 文档接口继续查找。
---

# Stock API Docs

## 适用场景

- 需要了解 MarketHub（中文名：合源）这套 API 怎么查文档、怎么确认参数、怎么定位具体接口。

## 使用方式

- 本地文档服务地址：`http://127.0.0.1:8801`
- 默认先读取：`GET /docs`

例如：

- `http://127.0.0.1:8801/docs`

## 文档查找规则

- 想先总览所有真实接口：`GET /docs/all`
- 想按关键词搜索接口文档：`GET /docs/search?q=...`
- 想读取某一篇具体文档：`GET /docs/<文档路径>`

例如：

- `GET /docs/stocks/quotes`
- `GET /docs/markets/calendar/trading`

## 说明

- 这项技能面向程序侧文档读取，统一使用 `/docs/...`
- 具体业务域、通用参数、日期格式、调用顺序，以 `GET /docs` 返回的内容为准
