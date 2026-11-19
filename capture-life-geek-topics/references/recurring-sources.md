# 更新源选用与验证

更新源用 RSS 或可扫新增的栏目发现选题；专题与机构指南按问题回查，论文/综述用于核证。不要把单篇或专题资料当定期产新源。本次未替换原四源入口，也不配置调度。

| `--source` | 适合找什么 | 读取与审核方式 |
| --- | --- | --- |
| `LS` | The Learning Scientists：复习、练习与学习决策 | 博客 RSS 优先取完整正文字段；合作招募、活动公告跳过。具体学习建议仍需核证。 |
| `KAB` | King Arthur Baking：原料替代、面团和烘焙操作 | RSS 只作发现，读取文章正文容器；`practical_source_only` 保留可读正文，哪怕少于 800 词。它是实操候选，不发给 `trace`，不进人体循证榜或资料包循证缺口排序。 |
| `BBM` | Barbell Medicine：普通人可用的测量、训练和动作 | 博客 RSS 优先取完整正文字段；与旧训练源先对主题。临床诊断或治疗不转成普通读者通用方案。 |

```text
python -B scripts/protocol_capture.py capture --source LS --source KAB --source BBM --per-source 2
```

新增三源按有效发布时间查新，未填有效时间的不猜；资料包模式的循证信源缺口仍优先于时间。入口的 `latest_published_at` 是当前 RSS 样本中最新的发布日期，可能是旧文章修订后重发，不证明内容首次出现。验证时分开报入口 HTTP/解析/发现数、正文方式和成功/失败、是否真正完成编辑核证。原始正文置于 `.local/`；只验接入时不 `remember`，全文不入库，也不据一次成功推断每周产量。
