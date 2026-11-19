# Failures and Recovery

## Represent Expected failures explicitly

- **Invalid input** violates the input contract; return safe validation details.
- **Domain rejection** is valid input rejected by a domain rule; return a typed outcome.
- **Conflict** means state or concurrency prevents the operation; return a typed conflict with safe retry guidance.
- **Not found** means an identity does not exist or is not visible; return one explicit, security-safe outcome.

Follow project convention with a result type, discriminated outcome, or typed exception. Use nullable success only for one normal no-value outcome; otherwise return the applicable Expected failure. Do not collapse distinct outcomes into a generic exception, `null`, or `false`.

## Propagate Faults with context

- A **transient dependency Fault** may recover within the operation deadline; apply the retry gate below.
- A **permanent dependency Fault** will not improve by repeating the same request; propagate it.
- **Resource exhaustion** prevents safe completion; reject or shed load without claiming success.
- An **invariant violation** reaches a forbidden state; detect, alert, and stop or isolate the unsafe operation.

Preserve the causal chain and operation context. Translate only at a real boundary and log once where reporting is owned.

## Retry gate

Retry only when all items are true:

- the Fault is classified as transient;
- repeating the operation is idempotent, or the dependency honors a deduplication key;
- attempts and total time are bounded within the caller deadline;
- backoff respects dependency limits;
- exhaustion preserves the causal chain and is observable.

Do not retry invalid input, domain rejection, invariant violations, or permanent dependency Faults.

## Atomicity and cleanup

- Acquire resources in an obvious order and release each once from the owning scope.
- Centralize cleanup when that scope has multiple exits.
- Use a transaction when an invariant requires all-or-nothing mutation.
- When atomicity is impossible, define the committed point, compensation, replay, and reconciliation before implementation.
- Never report success while required work is unknown or silently abandoned.

## Fallbacks

Use a fallback only when it preserves safety and domain rules. Mark degraded or stale results when relevant, and make activation observable and bounded; a fallback must not become a second implementation.

Fail closed for authorization, privacy, payment integrity, and destructive writes. Degrade gracefully only when the returned result remains truthful and safe.

## Verification

Exercise every applicable path:

- invalid input, domain rejection, and conflict;
- timeout, retry exhaustion, duplicate, and replay;
- failure after each acquired resource or partial mutation;
- cleanup, rollback, compensation, and reconciliation;
- logging redaction and user-safe messages;
- fallback activation and exit.

Do not treat an untested error path as handled.
