from __future__ import annotations

import argparse
import re
from pathlib import Path


EXPECTED_PAGES = {
    "index.html",
    "methodology.html",
    "reproduction.html",
    "execution.html",
    "robustness.html",
    "diagnostics.html",
    "appendix.html",
}

INDEX_REQUIREMENTS = (
    "总收益",
    "年化收益",
    "最大回撤",
    "卡玛",
    "夏普",
    "净值",
)

FORBIDDEN_CONTENT = (
    "数据修复",
    "排障",
    "MarketHub Adapter证据",
    "全部合同检查通过",
    "不可导出提示数",
    "preflight",
    "staging/",
    "contract-check",
    "backtest-tool.exe",
    "原生 Wasm",
    "结构化证据目录",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验策略报告结构、链接、首页指标和内容纯度")
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    validate_report(args.report_dir)
    print("strategy_report_validation=passed")
    return 0


def validate_report(report_dir: Path) -> None:
    pages = {path.name for path in report_dir.glob("*.html")}
    if pages != EXPECTED_PAGES:
        raise ValueError(f"报告页面不完整：{sorted(pages)}")

    combined = []
    for page in sorted(EXPECTED_PAGES):
        path = report_dir / page
        text = path.read_text(encoding="utf-8")
        combined.append(text)
        _validate_links(report_dir, page, text)

    full_text = "\n".join(combined)
    for term in FORBIDDEN_CONTENT:
        if term in full_text:
            raise ValueError(f"报告包含内部运行信息：{term}")
    if re.search(r"[A-Za-z]:\\", full_text):
        raise ValueError("报告包含绝对 Windows 路径")

    index = (report_dir / "index.html").read_text(encoding="utf-8")
    for label in INDEX_REQUIREMENTS:
        if label not in index:
            raise ValueError(f"入口页缺少关键内容：{label}")
    if not any(status in index for status in ("已验证", "部分验证", "研究设计", "失败")):
        raise ValueError("入口页缺少研究状态")
    if any(status in index for status in ("已验证", "部分验证")):
        if full_text.count("<img") < 6:
            raise ValueError("已回测报告少于6张决策型图表")
        for label in ("证据", "含义", "行动", "失效条件"):
            if label not in index:
                raise ValueError(f"入口页缺少四段式结论：{label}")


def _validate_links(report_dir: Path, page: str, text: str) -> None:
    for target in re.findall(r'(?:href|src)="([^"]+)"', text):
        if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        if not (report_dir / target).is_file():
            raise ValueError(f"{page} 的本地链接不存在：{target}")


if __name__ == "__main__":
    raise SystemExit(main())
