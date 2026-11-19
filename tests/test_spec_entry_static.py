"""STATIC contracts at the shipped entry/resource seam, not Pi/model execution."""

from pathlib import Path
import re
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "to-spec-to-done"


def text(path):
    return path.read_text(encoding="utf-8")


class SpecEntryStaticTests(unittest.TestCase):
    def assert_concepts(self, document, groups):
        """Accept alternative wording; check required topics, not model obedience."""
        for topic, pattern in groups.items():
            with self.subTest(topic=topic):
                self.assertRegex(document, re.compile(pattern, re.I | re.S), topic)

    def test_static_selected_directory_copy_keeps_required_resources_readable(self):
        # CC Switch copies the selected directory, not repository siblings.
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            copied = Path(tmp) / "to-spec-to-done"
            shutil.copytree(ENTRY, copied)
            entry = text(copied / "SKILL.md")
            for resource in ("engineering-quality.md", "auto-grilling.md"):
                relative = f"references/{resource}"
                self.assertIn(relative, entry, "entry must explicitly load shipped rules")
                self.assertTrue((copied / relative).is_file(), relative)
            rules = text(copied / "references/engineering-quality.md")
            self.assert_concepts(rules, {
                "loaded entry anchor": r"实际加载.{0,40}(父目录|目录)",
                "installed siblings": r"(安装|installed).{0,40}(兄弟|sibling)",
                "explicit root only": r"显式.{0,40}(技能根|skill root)",
                "missing is blocked": r"(缺失|不可读).{0,80}(blocked|阻塞)",
            })
            self.assertNotRegex(rules, r"[A-Za-z]:[/\\](?:Users|WILL)[/\\]")
            self.assertRegex(entry, r"\A---\nname: to-spec-to-done\ndescription: .+\n---\n")

    def test_static_entry_requires_grilling_and_readiness_before_publication(self):
        entry = text(ENTRY / "SKILL.md")
        self.assert_concepts(entry, {
            "investigation before grilling before publisher":
                r"##[^\n]*(调查|现状).*##[^\n]*grilling.*##[^\n]*(就绪|开工).*##[^\n]*to-spec",
            "unconditional local adapter": r"每次.{0,40}(必|执行).{0,60}grilling",
            "ready or blocked": r"首个可验收切片.{0,100}(blocked|阻塞)",
            "approved seam reuse": r"(复用|沿用).{0,40}(测试接口|seam)",
        })
        adapter = text(ENTRY / "references/auto-grilling.md")
        self.assert_concepts(adapter, {
            "real upstream": r"读取并执行.{0,40}grilling/SKILL.md",
            "frontier prerequisites": r"frontier.{0,60}前置.{0,40}成立",
            "all recommendations": r"自动采用.{0,20}全部推荐",
            "recompute": r"重算 frontier",
            "facts not guesses": r"无法验证.{0,60}(未知|blocked)",
            "proportional even when simple": r"(小|简单|明确).{0,100}不设置.{0,40}(问题|轮数)",
            "existing decisions": r"决定.{0,20}复用",
            "standalone preserved": r"单独调用原版.{0,40}交互",
            "authority remains separate": r"权限模式.{0,30}不构成.{0,20}授权",
            "fresh fact investigation": r"新上下文子 Agent",
        })

    def test_static_publication_contract_requires_readback_not_blind_retry(self):
        entry = text(ENTRY / "SKILL.md")
        self.assert_concepts(entry, {
            "publisher uses original template": r"to-spec.{0,100}原模板",
            "prior decision handoff": r"传入.{0,100}(决定|决策).{0,80}(假设|测试接口)",
            "search before create": r"(创建|发布)前.{0,60}(搜索|查找).{0,60}(复用|已有)",
            "unknown write result": r"(超时|结果未知).{0,100}回读.{0,100}(不|再).{0,20}(重试|创建)",
            "actual label": r"回读.{0,100}ready-for-agent",
            "body verification": r"回读.{0,100}正文.{0,100}验收",
            "native relationships": r"原生.{0,30}(sub-issues|子 Issue).*依赖方向",
            "legacy task graph": r"遗留 SPEC.{0,40}原 tickets.{0,40}不补交付段落.{0,40}不重新拆票",
            "failure preserves reality": r"(失败|无权|不可达).{0,100}保留.{0,100}(blocked|未完成)",
            "stop after publication": r"(结束|停止).{0,80}不启动.{0,80}to-tickets",
        })
        publication = entry.split("## A5 ", 1)[1].split("## A6 ", 1)[0]
        self.assert_concepts(publication, {
            "every new SPEC, not just its index": r"每个新 SPEC.*独立 SPEC.*子 SPEC.*总 SPEC",
            "seven-section publisher and proportionate coverage": r"原模板（七节）.*User Stories.*验收.*独立需求.*不设.*配额",
        })
        delivery = entry.split("## 写入 SPEC 的“执行与交付”\n", 1)[1]
        self.assert_concepts(delivery, {
            "stop, then separately authorize the portable host":
                r"发布并回读后停止.*另行获得实施授权后，由 `/skill:implement-spec-to-done` 主持",
            "delegation only": r"委托 `to-tickets`.*明确依赖.*委托 `implement-spec`.*不是替代入口",
            "explicit target": r"按项目指令显式指定 tracker",
            "acceptance and postmerge checks": r"测试.*独立审查.*验收通过.*SPEC 集成分支.*合入主干.*合并后验证",
            "owned, merged and clean only": r"仅清理.*明确归属.*已纳入主干.*工作树干净.*worktree.*分支",
            "normal push and governing remote": r"正常 push 主干.*源头核对实际远端.*本地主干.*tracking ref.*一致",
            "record readback before ordered close": r"回读完成记录.*先关闭 tickets.*后关闭 SPEC.*回读关闭状态",
            "failure preserves work": r"失败则停止.*保留成果.*报告阻塞.*不强删或强推",
        })
        self.assertNotIn("<spec-template>", entry, "template belongs only to original to-spec")

    def test_static_discovery_metadata_and_bundled_links_match_entry(self):
        metadata = text(ENTRY / "agents/openai.yaml")
        self.assert_concepts(metadata, {
            "discovery includes design gate": r"grilling",
            "publication scope": r"(仅|不启动|停止).{0,40}(SPEC|开发|发布)",
        })
        for document in (ENTRY / "SKILL.md", *sorted((ENTRY / "references").glob("*.md"))):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text(document)):
                # External targets are not bundled resources.
                if ":" in target or target.startswith("/"):
                    continue
                self.assertTrue((document.parent / target.split("#", 1)[0]).is_file(),
                                f"broken bundled link: {document.name} -> {target}")


if __name__ == "__main__":
    unittest.main()
