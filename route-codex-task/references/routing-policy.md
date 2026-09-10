# Routing policy

[`model-policy.json`](model-policy.json) is the sole machine-readable allow-list, rank table, and difficulty-floor table.

Choose the model from capability difficulty, reliability, and failure cost:

| Model | Use when |
| --- | --- |
| `gpt-5.6-luna` | Work is localized, reversible, and follows a known pattern. |
| `gpt-5.6-terra` | Work is bounded but unfamiliar or spans several coupled modules. |
| `gpt-5.6-sol` | Work is difficult, cross-repository, migration-heavy, public-contract-sensitive, or high consequence. |

Choose effort independently from reasoning complexity, ambiguity, coupling, search space, and verification burden:

| Effort | Use when |
| --- | --- |
| `light` | Reasoning is simple and the verification path is direct. |
| `medium` | Reasoning and verification are ordinary and bounded. |
| `high` | Reasoning is complex, coupled, ambiguous, or verification-heavy. |
| `xhigh` | Concrete evidence proves `high` inadequate for an exceptionally large or fragile reasoning problem. |

A strong model does not imply high effort. `xhigh` is especially slow and requires evidence, not merely a difficulty label.

Same-or-stronger is componentwise: both the model rank and effort rank must be greater than or equal to the recommendation. It is a compatibility fallback, not permission to recompute the route after task creation.

