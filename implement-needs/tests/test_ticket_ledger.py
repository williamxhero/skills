import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ticket_ledger import build_ticket_ledger

QUEUE = ["#433", "#434", "#437", "#439", "#438", "#414", "#415", "#416", "#417", "#418", "#419", "#420", "#421", "#422", "#427", "#428", "#391", "#392", "#393", "#435", "#441", "#394", "#395", "#396", "#397", "#398", "#399", "#401", "#442", "#404", "#408", "#429", "#443"]


class TicketLedgerTests(unittest.TestCase):
    def readback(self):
        lines = ["## 唯一单线队列", "`" + " → ".join(QUEUE) + "`", "### A0"]
        lines.extend(f"- [ ] {ticket} — title {ticket}" for ticket in QUEUE[:11])
        lines.append("### Strategy Genome")
        lines.extend(f"- [ ] {ticket} — title {ticket}" for ticket in QUEUE[11:16])
        lines.append("### Research Memory")
        lines.extend(f"- [ ] {ticket} — title {ticket}" for ticket in QUEUE[16:])
        issues = [{"number": int(ticket[1:]), "title": f"Issue {ticket}",
                   "state": "open", "url": f"https://github.com/williamxhero/Quant-Research/issues/{ticket[1:]}",
                   "body": "## Parent\n\nParent issue: #SPEC\n\n## Blocked by\n\nNone"}
                  for ticket in QUEUE]
        return {"root_issue": {"number": 444, "body": "\n".join(lines), "url": "https://github.com/williamxhero/Quant-Research/issues/444"}, "issues": issues}

    def test_builds_exact_33_item_ordered_github_ledger(self):
        ledger = build_ticket_ledger(self.readback())
        self.assertEqual(33, len(ledger["tickets"]))
        self.assertEqual(QUEUE, ledger["queue"])
        self.assertEqual("github", ledger["source"]["kind"])
        self.assertEqual(QUEUE, [entry["ticket_id"] for entry in ledger["tickets"]])

    def test_rejects_incomplete_issue_readback(self):
        readback = self.readback()
        readback["issues"][0]["state"] = "closed"
        readback["issues"] = readback["issues"][:-1]
        with self.assertRaisesRegex(ValueError, "missing"):
            build_ticket_ledger(readback)

    def test_maps_historical_delivery_records_to_structured_evidence(self):
        readback = self.readback()
        readback["issues"][0]["state"] = "closed"
        readback["delivery_records"] = [{
            "ticket_id": "#433", "status": "closed",
            "commits": [{"sha": "abc123"}],
            "tests": [{"report": "artifacts/433.xml"}],
            "acceptance": ["https://github.com/williamxhero/Quant-Research/issues/433#comment-1"],
            "evidence": ["file:///history/433.json"],
        }]
        ledger = build_ticket_ledger(readback)
        entry = ledger["tickets"][0]
        self.assertEqual(["git:abc123"], entry["commits"])
        self.assertTrue(entry["tests"][0].startswith("file:///"))
        self.assertEqual("closed", entry["status"])

    def test_rejects_orphan_historical_delivery_record(self):
        readback = self.readback()
        readback["delivery_records"] = [{"ticket_id": "#999", "status": "closed"}]
        with self.assertRaisesRegex(ValueError, "outside the queue"):
            build_ticket_ledger(readback)

    def test_blocked_by_ignores_ticket_mentions_after_single_line_declaration(self):
        readback = self.readback()
        issue = next(issue for issue in readback["issues"] if issue["number"] == 434)
        issue["body"] = (
            "## Parent\n\nParent issue: #SPEC\n\n## Blocked by\n\n"
            "#433。只依赖其来源/范围交付，不依赖 #414/#415/#420。\n"
        )

        ledger = build_ticket_ledger(readback)

        entry = next(entry for entry in ledger["tickets"] if entry["ticket_id"] == "#434")
        self.assertEqual(["#433"], entry["blocked_by"])

    def test_blocked_by_retains_multiple_declared_blockers(self):
        readback = self.readback()
        issue = next(issue for issue in readback["issues"] if issue["number"] == 434)
        issue["body"] = (
            "## Parent\n\nParent issue: #SPEC\n\n## Blocked by\n\n"
            "#433\n#437\n\nOnly these two are prerequisites; #414 is not.\n"
        )

        ledger = build_ticket_ledger(readback)

        entry = next(entry for entry in ledger["tickets"] if entry["ticket_id"] == "#434")
        self.assertEqual(["#433", "#437"], entry["blocked_by"])


if __name__ == "__main__":
    unittest.main()
