"""STATIC public-source contracts only; no Pi/model or deployment evidence."""

from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "improve-codebase-architecture-to-done"


def text(path):
    return path.read_text(encoding="utf-8")


class ArchitectureEntryStaticTests(unittest.TestCase):
    def assert_topics(self, document, topics):
        for topic, pattern in topics.items():
            with self.subTest(topic=topic):
                self.assertTrue(re.search(pattern, document, re.I | re.S), topic)

    def test_static_public_entry_and_copied_sibling_dependencies_are_explicit(self):
        entry = text(ENTRY / "SKILL.md")
        frontmatter = entry.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name: improve-codebase-architecture-to-done$")
        self.assertNotRegex(frontmatter, r"(?i)draft|草案")
        self.assert_topics(entry, {
            "source is not deployment": r"源码.*(STATIC|静态).*(未部署|未安装)",
            "loaded entry anchor": r"实际加载.{0,40}(父目录|目录)",
            "compatible installed sibling required": r"兼容.{0,40}to-spec-to-done.*(缺失|不可读).*blocked",
            "explicit supplied root only": r"显式.{0,40}skill root",
            "actual reads, not fictional invocation": r"读取.{0,40}SKILL.md.*必需参考.*执行.*Skill API",
        })
        metadata = text(ENTRY / "agents/openai.yaml")
        self.assertNotRegex(metadata, r"(?i)draft|草案")
        for name in (ENTRY.name, "to-spec-to-done", "implement-spec-to-done"):
            self.assertIn(name, metadata)
        # Reproduce selected-directory copying, not an installation or live resolution.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (ENTRY.name, "to-spec-to-done", "implement-spec-to-done"):
                shutil.copytree(ROOT / name, root / name)
            copied = root / ENTRY.name / "SKILL.md"
            targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text(copied))
            for required in (
                "../to-spec-to-done/references/engineering-quality.md",
                "../to-spec-to-done/references/auto-grilling.md",
                "../implement-spec-to-done/references/pi-execution.md",
            ):
                self.assertIn(required, targets)
            for target in targets:
                self.assertTrue((copied.parent / target).is_file(), target)
            shutil.rmtree(root / "to-spec-to-done")
            self.assertFalse((copied.parent / "../to-spec-to-done/references/engineering-quality.md").exists())
        self.assertEqual(sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("SKILL.md")),
                         ["implement-spec-to-done/SKILL.md", "improve-codebase-architecture-to-done/SKILL.md",
                          "to-spec-to-done/SKILL.md"])

    def test_static_candidate_phase_is_scoped_visual_and_not_standalone_grilling(self):
        entry = text(ENTRY / "SKILL.md")
        self.assert_topics(entry, {
            "C1 bounded baseline": r"## C1 .*授权.*基线.*近期.*(commit|SHA).*摩擦.*测试",
            "explicit phase boundary": r"LOCAL CANDIDATE.PHASE ADAPTATION.*原.*[12].*原.*[3].*(跳过|不执行)",
            "real original plus scaffold": r"improve-codebase-architecture/SKILL.md.*HTML-REPORT.md.*scaffold",
            "fresh completed exploration": r"新上下文.*探索.*(输出|事件).*退出码",
            "deletion reasons not aesthetic proxy": r"deletion test.*(消失|散落).*locality.*leverage",
            "facts versus proposal": r"现状.*拟议.*未实施.*(接口|interface)",
            "real diagrams not a claim": r"(Before|before).*Proposed.*(图|diagram).*(无图|没有图).*不.*可视",
            "visual route prepares first": r"visual-explainer.*prepare.*目录.*源文件.*render",
            "original interaction intact": r"原版.*(不修改|保持).*单独.*(交互|选择)",
            "no premature interface design": r"不提前.{0,30}(设计|确定).{0,20}完整.{0,20}接口",
        })

    def test_static_selection_distinguishes_no_change_from_missing_prerequisites(self):
        entry = text(ENTRY / "SKILL.md")
        selection = entry.split("## C3 ", 1)[-1].split("## C4 ", 1)[0]
        self.assert_topics(selection, {
            "one evidence backed candidate": r"自动.*(首选|推荐).*一个.*不可分.*(不|不是).*全部",
            "normal evidence backed no change": r"no-change.*(范围|基线).*(排除|收益).*正常.*(停止|结束)",
            "no writes on no change": r"no-change.*不.*(发布|SPEC).*(拆票|tickets).*实施",
            "blocked facts authority and capability": r"blocked.*事实.*(权限|授权).*能力",
            "not speculative default": r"Speculative.*(不|不能).*自动.*(推进|实施)",
            "resume without restarting scan": r"恢复.*(回读|核对).*候选.*(不|避免).*重扫",
        })

    def test_static_handoffs_preserve_single_spec_authority_and_preclose_acceptance(self):
        entry = text(ENTRY / "SKILL.md")
        self.assertEqual(re.findall(r"(?m)^## C([1-7]) ", entry), list("1234567"))
        spec = entry.split("## C4 ", 1)[-1].split("## C5 ", 1)[0]
        self.assert_topics(spec, {
            "wrapper owns sole spec grilling": r"to-spec-to-done.*唯一.*规格级 grilling.*(一次|一处)",
            "only original publisher": r"仅由.*to-spec.*原模板.*(生成|发布)",
            "benefits are agreed acceptance": r"收益.*基线.*(阈值|判定).*兼容.*迁移.*(恢复|回退).*验收",
            "published readback required": r"回读.*正文.*(关系|标签).*交付",
        })
        handoff = entry.split("## C5 ", 1)[-1].split("## C6 ", 1)[0]
        self.assert_topics(handoff, {
            "live identity not link alone": r"源头回读.*(身份|仓库).*正文.*状态",
            "authorization separate": r"发布.*不.*实施授权.*(范围|漂移)",
            "no duplicate authority": r"不.*(重建|复制).*SPEC.*tickets.*(计划|状态)",
        })
        delivery = entry.split("## C6 ", 1)[-1].split("## C7 ", 1)[0]
        self.assert_topics(delivery, {
            "only wrapper for current spec": r"implement-spec-to-done.*仅.*当前 SPEC",
            "B3 is reality recheck": r"B3.*(现实|环境).*复核.*不.*(访谈|设计)",
            "accept all architecture requirements before closing": r"B9.*收益.*兼容.*迁移.*(恢复|回退).*关闭前.*验收",
            "failures remain here": r"失败.*当前 SPEC.*不.*换候选",
        })
        report = entry.split("## C7 ", 1)[-1]
        self.assert_topics(report, {
            "closed readback": r"回读.*主干.*远端.*(CLOSED|关闭).*关闭前.*证据",
            "no late acceptance substitute": r"不.*(补验收|替代).*关闭前",
            "stale evidence restores true state": r"过期.*(恢复|核验).*状态",
            "bounded completion": r"不自动.*(重扫|扫描).*重构",
        })


if __name__ == "__main__":
    unittest.main()
