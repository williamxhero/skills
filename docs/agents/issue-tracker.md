# Issue tracker: GitHub

Specs and tickets live in `https://github.com/williamxhero/skills/issues`. This repository is owned by the user. Always include `--repo williamxhero/skills` for issue operations.

- Publish a spec: `gh issue create --repo williamxhero/skills --title "SPEC: ..." --body-file <utf8-file> --label ready-for-agent`.
- Read back the full result: `gh issue view <number> --repo williamxhero/skills --json number,title,body,state,labels,comments,url`.
- Search existing issues before creation; reuse the exact existing SPEC instead of duplicating on retries.
- Use GitHub native sub-issues and issue dependencies when a SPEC needs them; Markdown links alone are not their substitute.
- gh 2.89 supports API fallback: obtain the issue numeric database ID, then POST `repos/williamxhero/skills/issues/<parent>/sub_issues` with `sub_issue_id`, or POST `repos/williamxhero/skills/issues/<child>/dependencies/blocked_by` with `issue_id`. Read back each relationship and its direction.
- Close only after the SPEC completion method and delivery evidence are satisfied. Publication alone never authorizes implementation or closure.

The GitHub SPEC is the canonical live task. A file under `docs/specs/` is a versioned publication snapshot, not a second issue tracker. PRs are not a separate requirements intake surface.
