# TEXT/manual source walkthrough — prerequisite delivery

This is manual SOURCE reasoning, **not actual Pi/model/runtime/platform execution** or a fixture result. Symbols are hypothetical obligations, not seeded Done records. Authority remains [SPEC #10](https://github.com/williamxhero/skills/issues/10), its comments and native ticket graph; this matrix is neither an executor nor a second tracker.

Source examined: integrated `e43471d50887423e52a58ec6c63b36a6c849a8e3`, with unchanged [entry B1–B12](../implement-spec-to-done/SKILL.md) and [mandatory contract](../implement-spec-to-done/references/prerequisite-delivery.md). Entry SHA256 `19ad70acaff27238f89d804fe1b819964f85349e9110fde4f32f65db2335024d`; contract SHA256 `1d08f777273b920175d2d95e9c006461fd61261603a371f553b7667169553290`. This documentation slice changes neither source. Exact final documentation commit/tree, commands and per-file hashes are recorded externally in `text-finish-ticket12-complete-report.md`; later review/delivery belongs in the formal Issues.

Rule pointers below name the contract's sections: **Sources** = “先读完整来源，再决定是否可写”; **Safety** = “完整闭包安全门禁”; **Delivery** = “证据、选择与完整交付”; **Refresh** = “每次返回／恢复重新读源头”. These are pointers, not another vocabulary authority; definitions stay in [GLOSSARY.md](../GLOSSARY.md).

## Reasoned cases

`T ← A` means T requires A; letters denote SPECs unless marked ticket. Before any new delivery write (including ticketing, product, branch/integration or completion records), read the whole relevant finite closure, complete bodies/comments, roles/contracts, authority, containment/inverse edges and delivery evidence. Deduplicate by repository + stable ID per snapshot. Each permitted CURRENT below is conditional on that complete safety check. Read-only investigation and truthful blocked reporting preserve known progress; blocked cases permit no new delivery writes or graph manipulation.

| Case | Source rule | Expected choice / stop, writes and required evidence |
| --- | --- | --- |
| Deep chain `T ← A ← B ← C` | Sources; Delivery; B12 | CURRENT C, then B, A, T only after each predecessor's full delivery and completed CLOSED readback. Keep final target T and one CURRENT; no early downstream tickets/product writes; existing downstream tickets stay unstarted. |
| Diamond / multiple incoming `T ← A,B,D`; `A,B ← C` | Sources; Delivery; Refresh | Inspect all branches, C once per snapshot; C precedes A/B and is delivered once. D competes only when eligible under the order/tie rule; all CURRENTs remain serial, T last. Reuse refreshed C evidence, not repeat its work; no T writes until every required branch has delivered proof. |
| Stable tie / explicit order: `T ← A,B` | Delivery | If equivalent and unordered, canonical `(normalized repository, stable ID string)` lexical order decides (same repo IDs `10` before `2`, not numeric business priority). Explicit business B-before-A overrides the tie; dependency A←B also forces B. Record reason; never parallel CURRENTs. |
| Total `U` requires child SPECs A,B plus incoming C; contains implementation ticket u1 | Sources; Refresh | Include A/B/C and U's independent total acceptance; u1 is not a child SPEC loop. After children delivered, independent integration makes U CURRENT through B2–B11; otherwise verify complete total obligations/records before B11. Child CLOSED alone cannot complete U. |
| Supplied ticket t, unique verified owner S | Sources; Refresh | Final target remains t, CURRENT uses S after its prerequisites. Full body/role and inverse containment establish owner; S delivery must prove t acceptance, not silently substitute S or invent a ticket-shaped SPEC. |
| Cross-SPEC `S:t ← Q:q`; `Q ← C` | Sources; Safety | Verify q's unique owner Q and its authority, then C/Q delivery before dependent work. Cross-repository Q is allowed only with verified scope authority; local permission proves no real external authority. No owner/sibling sweep. |
| Same-SPEC `S:t2 ← S:t1` | Sources; B5/B11 | Integrated AND independently verified t1 releases t2 while both remain OPEN. Real #11→#12 uses this frontier; no per-ticket full-SPEC loop or premature close. |
| Cycle `T ← A ← B ← A`; self `T ← T` | Safety | STOP with exact paths `A ← B ← A` or `T ← T`; preserve records, request authoritative correction of that contradiction, never delete edges/cancel/close to manufacture readiness. |
| Inconsistent containment / ambiguous owner | Sources; Safety | t claims owner S but inverse says Q; or both `S contains t` and `Q contains t` claim ownership. STOP naming these paths and t; require authoritative unique consistent ownership, not reparent/relabel/guess. |
| Whole-closure gates `T ← A,G`, runnable A | Sources; Safety | For each G = unknown edge, unreadable source, human/manual action, external action, unowned node, TEST ONLY, uncertain authority, or missing mandatory capability: STOP ALL new delivery writes, including A. Unknown ≠ empty. Report path T←G, distinguish missing facts/capability/permission and require the actual missing source, action, ownership, authority or capability; labels/recommendations do not unlock it. |
| Invalid CLOSED `T ← A` | Delivery | Cancelled/not-planned A, or CLOSED without acceptance/Git/records/completed proof, is not delivered. STOP; only authorized truthful recovery with real missing evidence can unlock T; never convert platform status to success. |
| Delivered reuse / target no-op | Delivery; Refresh | If source verification establishes A's applicable acceptance, Git delivery/remote equality, complete records and completed CLOSED, reuse identity/evidence without reopening, record rewrite, duplicate tickets, extra TDD or product edits. If T and all obligations also verify, no-op unless new facts invalidate proof; these are conditions, not asserted fictional deliveries. |
| Selected CURRENT A fails mandatory stage (e.g. B8) | Delivery; B9–B11 | STOP at A, preserve target T incomplete, stage/diagnosis/progress and minimum continuation condition; no dependent writes, branch-switch to B for green, or Done record. A different passing check cannot replace the failed gate. |
| After close / resume / new edge / reopen | Refresh; B12 | After A completed CLOSED or on resume, reread original T and whole remaining graph/state/roles/evidence/authority. If new `T ← C` or A reopens, invalidate stale selection before tickets/product writes and recompute; shared proof is refreshed, cached plans/pointers do not authorize continuation. |
| Unknown external write result | Refresh; B4/B11 | Before a retry decision, source GET full actual state, all-state full-body identity/coverage dedup and relevant graph/inverse readback. Preserve partial success/errors; no blind duplicate ticket/edge/record mutation. |
| No outgoing / parent / sibling expansion | Sources | With `D ← T`, parent P and unrelated sibling X, D/P/X are not new targets; inverse reads corroborate direction only. Verified owning scope/required total children remain included, not arbitrary adjacent work. |
| Every actually entered SPEC | Delivery; entry B2–B11 | Full method/capability/authority preflight, original grilling, qualified publisher reuse/readback, fresh isolated implementer + DIFFERENT merger, stable pipeline simplify, genuine conditional single-issue compound judgment, independent parallel single-axis final review, fresh acceptance, normal main/postmerge/owned clean ancestor-safe cleanup/push/actual remote equality, full OPEN records→ticket close/read→SPEC completed close/read. Prose is no-code, not fake TDD; authored code retains genuine public RED→minimal change→GREEN. Failure stops dependent stages. |

## Parent's 22 criteria — evidence coverage, not a completion checklist

All source choices/stops above are TEXT only. Existing [supplemental STATIC source](test_prerequisite_delivery_static.py) checks B1's shipped mandatory pointer, alongside the four current original proof files / 14 legacy checks; it does not interpret these cases. No new test, framework, fixture or model run is added. [Acceptance index](../docs/acceptance.md#p12-proportionate-text-checkpoint) co-locates history and current evidence; independent final review and actual outer delivery remain pending, never self-certified by this matrix.

| Parent criterion | Source / TEXT / evidence or unresolved gate |
| --- | --- |
| 1 | Sources/Refresh; chain, ticket and resume cases retain original target and one CURRENT. |
| 2 | Sources/Safety; complete-closure, diamond, total and no-expansion cases. |
| 3 | Current STATIC + this TEXT; old443 selected CORE (no semantic RED), assisted recovery5 old completion, failed strengthened repeat and never-run fresh chain stay historical; independent final review / formal delivery pending. |
| 4 | Delivery; deep chain case, full B2–B11 for each actual CURRENT, not executed hypothetical lifecycles. |
| 5 | Sources/Delivery; diamond/shared-once/target-last and no early downstream writes. |
| 6 | Delivery; stable lexical tie, explicit business/dependency order, serial CURRENT case. |
| 7 | Delivery/Refresh; conditional verified reuse/target no-op, no manufactured activity. |
| 8 | Sources/Refresh; total child obligations versus tickets and independent total acceptance. |
| 9 | Sources/B5; supplied ticket, cross-SPEC owner, ambiguous owner and same-SPEC frontier cases. |
| 10 | Safety; exact cycle/self/containment paths and minimum correction, no graph manipulation. |
| 11 | Safety; whole-closure G alternatives block even runnable A; facts/capability/permission separated. |
| 12 | Delivery; invalid CLOSED/cancelled case requires authority and genuine evidence. |
| 13 | Delivery; selected mandatory failure preserves CURRENT/target and STOP. |
| 14 | Refresh/B12; after-close/resume/new-edge/reopen case, source recomputation. |
| 15 | Entry B2–B11; actual #11 merger integration e434 releases #12 internally; this fresh docs implementer does not replace the required different merger or outer roles. |
| 16 | Entry B6/B7; protected current proofs unchanged; actual parent pipeline simplify/conditional compound judgment still pending, prose no-code does not exempt authored test code. |
| 17 | Entry B8; pending parallel independent fresh Standards/Spec, fixed base `443fe320f875f6edcf8f37bacccf8b146b4c249f` / final candidate, full norms/original 12-smell baseline/full SPEC/comments, each report <400 words; resolve findings and refresh evidence. |
| 18 | Entry B9–B11; fresh verification, normal main/postmerge, owned clean ancestor-safe cleanup/push/local-tracking-remote equality, truthful full OPEN records, tickets then SPEC completed CLOSED readbacks remain outer gates. |
| 19 | Existing separate supplemental +14 checks; exact four current proof files and pinned 52 originals/licenses/provenance/snapshots preserved, including preexisting user inventory extensions; STATIC only. |
| 20 | B1/B12 mandatory single local reference, metadata and four-definition glossary remain unchanged; only these docs reconcile method, no new vocabulary/scheduler. |
| 21 | Acceptance P11/P12 co-locate failure/recovery history, versioned evidence/pending gates; original AR OPEN/BLOCKED remains distinct from prospective AP/AP-R. |
| 22 | Draft/STATIC/TEXT, historical actual Pi/local tracker/local bare, independent review, real GitHub source delivery and managed installation are separate; source change is no deployment authorization. |
