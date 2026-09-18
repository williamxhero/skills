# Versioned delivery evidence

Use `controller.py record-delivery-receipt` for controller-owned CI evidence. A
receipt is applicable only when repository identity, target SHA, test plan and
selection, environment fingerprint, acceptance version, validator version, result,
provenance, source URI, and observation time all match the requested key.

`worker_claim` is never a trusted source. Keep the claim as a candidate pointer and
record the independently read CI receipt separately. `controller.py
validate-delivery-receipt` returns `allow` only for a matching passed
`controller_ci` receipt; it reports the smallest mismatch set otherwise. Raw logs
remain addressable through `source_uri`.

PR head, merge commit, and final release candidate are separate target SHAs. A head
receipt can be reused for another SHA only when the receipt explicitly carries an
equivalence policy that the verifier can check; a syntactically valid test URI alone
does not prove applicability.
