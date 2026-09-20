P0-4 总验收：通过。

- Child tickets #58、#59、#60 均已独立完成、写入 commit/test/acceptance 证据并关闭。
- Merge commit: `afbe3ce`，包含原子显式事务、业务版本/stale-state、业务 evidence 引用、独立 observation 流、create/open-existing/read-only 模式与只读导出。
- Full regression: `python -m unittest discover -s tests -v` → 211 passed。
- Cross-SPEC acceptance: 事务失败全回滚；旧业务版本拒绝写入；观测不能推进业务版本；证明/豁免进入业务事件；只读路径不创建父目录、WAL/SHM 或 schema。

Commit evidence: commit:afbe3ce
Test evidence: test:unittest-211-green
Acceptance evidence: acceptance:#44

