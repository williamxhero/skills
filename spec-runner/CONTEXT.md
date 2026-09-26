# Spec Runner Context

Spec Runner coordinates durable work from intake through execution, recovery,
and delivery while preserving evidence for every externally visible result.

## Run lifecycle

**Run**:
A durable execution identity for one normalized brief and configuration. A Run
can pause, wait, recover, resume, or finish without changing its identity.
_Avoid_: job, attempt

**Stage**:
A named phase of a Run that owns one kind of progress, such as planning,
implementation, review, recovery, or delivery.
_Avoid_: step when referring to the domain phase

**Recovery episode**:
The durable record of one unresolved failure domain for a Run and Stage,
including observations, decisions, budget, and the next safe condition.
_Avoid_: retry, error log

## Production delivery

**SPEC**:
A dependency-ordered delivery unit with its own tickets, candidate, checks,
review, and delivery evidence.
_Avoid_: feature, batch

**Takeover**:
A controlled adoption of an existing Run or source thread that preserves
verified progress and establishes one new owner for the remaining work.
_Avoid_: fork, clone

**Delivery receipt**:
Durable evidence that binds a Run or SPEC operation to the observed external
object and its verified result.
_Avoid_: log, status flag
