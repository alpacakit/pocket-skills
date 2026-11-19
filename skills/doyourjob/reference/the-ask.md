# The Ask

Use this file only when a genuine judgment remains. Build the decision first, then include only information that helps the owner answer it.

## Decision block

Translate the labels into the user's language as directed by `SKILL.md`. Keep this order and omit optional fields with no material content:

```text
[State]          Current truth; adverse and unchecked facts first.
[Decision]       One decision, why this owner must make it, its waiting cost,
                 and reversal cost.
[Objection]      A material live objection and its premise. Optional.
[Options]        Either consequence-rich options or one fixed criteria comparison;
                 include priced inaction when real.
[Recommendation] One option.
[Reason]         The load-bearing premise on a separate line.
[Falsifier]      If <X> is false, the recommendation is wrong and <Y> is right.
[Evidence]       Evidence status for load-bearing claims and confidence in the call.
[Next]           The next authorized move and timing for every answer.
```

When a recommendation exists, **Recommendation**, **Reason**, and **Falsifier** are mandatory. When no judgment remains, do not use this block.

## Build consequence-rich options

Use this format unless the criteria-frame trigger below applies. Give every option a meaningful name and three consequences:

- **Gain:** the outcome it creates.
- **Sacrifice:** what it gives up.
- **Owner cost:** money, calendar, attention, or displaced work.

Include doing nothing when available and price the delay. If it is unavailable, state why in one clause. Letters may be reply shortcuts after the descriptions; never present bare A/B/C labels.

## Compare multi-criteria judgments on one fixed frame

Use a shared criteria frame when several criteria could change the owner's choice. Otherwise use consequence-rich options and the falsifier.

Choose only criteria that could change the decision. Name each after the outcome, constraint, or resource being compared, such as deadline margin, recovery time, or cash cost. Separate hard constraints and vetoes before comparison. Define direction, scope, and time horizon when they are not obvious; never omit a criterion that could reverse the recommendation.

Compare every viable option against every criterion. Each option-criterion pair must contain a supported value or range, `unknown`, or `not applicable`. Use a compact table when readable; otherwise use parallel bullets with identical criterion labels and order.

Replace the ordinary option descriptions with this one comparison; do not repeat them. Do not invent weights or collapse the result into a total score. State the priority assumption behind the recommendation and the criterion or unknown that could reverse it. The comparison informs a preference; it is neither evidence nor the decision itself.

## Separate recommendation, reason, and falsifier

Use this shape:

> **Recommendation:** Patch.
>
> **Reason:** The general case is not worth delaying the active migration by two weeks.
>
> **Falsifier:** If the migration has already slipped, Patch is wrong and Rewrite is right.

To find the falsifier, ask which premise must fail for another option to win. If none can, move the item to **Decide**. If many can, identify the one carrying the most weight, preferably a premise the owner can evaluate from context unavailable to you.

## Preserve disagreement and evidence

State a material objection and the premise it challenges; do not flatten it into "both options have merits." Apply `reference/confidence.md` to load-bearing claims and numbers. Keep fact confidence separate from whether the judgment itself is close.

## Make the reply operational

State what follows from every answer without promising an action outside the authorized scope. Prepare the next artifact or approval step when execution authority is absent.

Close on the decision or premise needed:

> Choose **Rewrite**, **Patch**, or **Wait**. If the reason is wrong, name the premise that changes it.

Do not close with "Please confirm," "Let me know what you think," or another generic approval request.
