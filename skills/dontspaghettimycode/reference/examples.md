# Decision Examples

Apply the decision pattern, not the names or architecture.

## Several symptoms point to one distorted fact

- **Symptoms:** UI, billing, and access control disagree about whether a subscription is “active”; support repairs accounts manually; every grace-period change touches several modules.
- **Distortion:** One `is_active` flag collapses lifecycle, payment, cancellation, and entitlement into one ambiguous fact. The repeated conditions are compensations, not four separate bugs.
- **Owner:** Model lifecycle, payment status, and entitlement window as distinct facts. Let one entitlement policy derive whether access is currently allowed.
- **Why:** Correct the fact the system is built around; centralizing the ambiguous boolean would preserve the distortion behind a new layer.

## A new case reshapes the branch it joins

- **Problem:** A renewal banner grew one branch per request: show to trial users whose trial ends within 7 days unless dismissed; show to paid users whose card expires within 7 days unless dismissed. A new request hides it for team members whose admin pays, and the easy edit is another branch in front or a `hideForTeam` parameter that one caller passes.
- **Owner:** The branches answer one question: does this viewer have a payment deadline to act on soon? Let one function derive that deadline, and show the banner when it falls within 7 days and was not dismissed. A member whose admin pays has no deadline, so the new case needs no branch of its own, and the repeated window and dismissal checks become one.

```text
Before: team member and admin pays -> hide   (new, inserted first)
        trial and trial ends within 7 days and not dismissed -> show
        paid and card expires within 7 days and not dismissed -> show
        else -> hide
After:  deadline = trial end | card expiry | none when someone else pays
        show = deadline within 7 days and not dismissed
```

- **Why:** The special case disappears into the fact the decision is built on instead of stacking in front of it. A guard clause remains the right shape for a real precondition of the whole decision; avoid branches whose only reason is the order requests arrived. Stop at the cases that exist: a rule table, plugin interface, or configuration would be indirection for imagined needs, and a similar banner elsewhere that changes for a different reason stays separate.

## An Owner is not a forwarding layer

- **Problem:** `EmailAddress.parse` already enforces the email invariant, but a new `EmailPolicyService` and interface are proposed only so the rule appears to have an Owner.
- **Owner:** Keep `EmailAddress.parse` as the Owner and call it directly from the boundary adapter.
- **Why:** An Owner is the smallest place that enforces a decision, not an architectural layer or a forwarding name.

## One service is not necessarily one Owner

- **Problem:** One `AccountService.execute(mode, input)` handles password eligibility, refunds, and subscriptions through caller-specific modes, shared mutable context, and one broad transaction. Each branch changes for a different business reason, and testing one requires every dependency.
- **Owner:** Give each rule and transition its own authority and dependencies. Keep a shared entry point only as a boundary adapter, and share a transaction only when one explicit invariant requires atomicity across the operations.
- **Why:** Physical colocation does not make unrelated decisions cohesive; it creates change collision when they must be understood, modified, or tested together.

## Break compatibility when it has no job

- **Problem:** Private event flags permit invalid states; all producers, consumers, and retained events are controlled and can move in one deployment, so a legacy decoder would serve no user or rollout need.
- **Owner:** Replace the flags with one status model, update producers, consumers, stored fixtures, and tests together, and remove the old decoder.
- **Why:** Existing shape is not a requirement. With no reliance or overlap to preserve, compatibility would only create a second representation and delay completion.

## External rows are not domain objects

- **Problem:** CSV columns pass directly into `Order` and persistence, so source-specific missing values, currencies, duplicate IDs, and schema versions acquire accidental domain meaning.
- **Owner:** Let `SourceOrderAdapter` own the source contract and `Order.create` own order invariants.

```text
Before: row -> Order(row) -> persistence
After:  row -> validate source -> translate units and identity -> Order command -> domain validation -> persistence
```

- **Why:** Keep external meaning at the boundary instead of letting it redefine Canonical data.

An export adapter may look similar but own different validation, lifecycle, and compatibility. Keep them separate when they change for different reasons.

## Omitted is not cleared

- **Problem:** An update field accepts missing (`undefined`), `null`, or a value, but passing that representation inward makes callers disagree over whether no value means "leave unchanged" or "clear."
- **Owner:** Let the update adapter own the wire meanings and emit explicit commands; let the domain Owner decide whether each transition is valid.

```text
missing -> KeepEmail
null    -> ClearEmail
value   -> SetEmail(value)
command -> Account.update(command) -> next | Expected failure
```

- **Why:** Preserve legitimate absence while preventing boundary sentinels, defaults, and null checks from becoming domain policy.

## A schema change is deferred without scattering the rule

- **Problem:** Legacy `is_draft`, `is_published`, and `is_archived` columns permit impossible combinations, but external readers prevent an immediate schema migration.
- **Owner:** Let `DocumentLifecycle` own valid state and transitions. Let `LegacyDocumentAdapter` own only the temporary legacy encoding, and route every write through `DocumentLifecycle`.

```text
Read:   legacy flags -> validate and translate -> DocumentStatus
Write:  transition(current, command) -> next | Expected failure
        next -> persistence adapter -> one valid legacy encoding
Guard:  reject invalid flag combinations and test every legacy encoding
Remove: adapter after legacy-column reads stay at zero for one full reporting cycle
```

- **Why:** Introduce the correct model without creating a second decision Owner, while making the guard, test, and observable removal condition explicit.

## A measured read model duplicates safely

- **Problem:** A normalized transactional query misses a measured latency SLO, and developers propose duplicating totals into several writable tables.
- **Owner:** Keep transactions Canonical and let one projector own the rebuildable read model.

```text
Before: orders.total + customers.order_total + dashboard.order_total
After:  Canonical orders -> projector -> rebuildable customer_order_totals
```

- **Why:** Meet the measured need without creating multiple writable sources of the same fact.
