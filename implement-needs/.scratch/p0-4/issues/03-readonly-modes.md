# P0-4.3: 真实只读连接模式与导出

## Parent

SPEC P0-4: https://github.com/williamxhero/skills/issues/44

## What to build

区分 create、open-existing 和 read-only 数据库连接。只读访问缺失路径必须失败，不能创建父目录、数据库、WAL、schema 或 metadata；导出命令使用只读模式并保持成功/失败路径均无数据库副作用。

## Acceptance criteria

- [ ] create 模式可初始化新库，open-existing 模式不会悄悄创建缺失库，read-only 模式对缺失库直接失败。
- [ ] read-only 打开已有库不会写入 WAL、schema 或 metadata，且能拒绝损坏/不完整 schema。
- [ ] 导出命令使用 read-only 连接；缺失路径和诊断失败后不会留下目录或空库。

## Blocked by

- P0-4.1: 原子业务事务与乐观业务版本
- P0-4.2: 分离业务事件与观测游标

