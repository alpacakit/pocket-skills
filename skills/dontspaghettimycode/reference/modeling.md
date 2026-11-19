# Data Modeling

## Inspect the current structure first

Inspect:

- the conceptual model, domain invariants, and intended Owner;
- the actual schema, records, types, keys, constraints, defaults, indexes, views, and triggers;
- representative data, volume, invalid combinations, historical meaning, and known anomalies;
- migrations, ORM or serialization mappings, and every relevant read and write path.

Do not infer intent from an ORM model or table alone. State the gap between the required model and current representation before choosing a correction.

## Classify each representation

- **Canonical** data is the sole writable source of a fact; protect its integrity and migration.
- A **Replica** copies a Canonical source elsewhere; name its provenance and synchronization.
- **Derived** data and caches must name their source, rebuild path, freshness, and invalidation.
- A **Snapshot** is copied once and deliberately frozen at a named event; it must not track its source.
- **Input** is untrusted until validated and, where meanings differ, translated.
- A **View** is a consumer-specific projection; do not move domain rules into it.

Persistence alone does not make a local database Canonical. If another system remains authoritative, treat the local copy as a Replica or cache.

A snapshot such as an order's charged price is Canonical for that historical fact, not an outdated copy of the product's current price; retain a source reference for audit when useful.

## Define an input contract

Before importing a file, response, message, or external table, determine:

- authority, provenance, identity, and duplicate policy;
- schema, version, types, units, encoding, and timezone;
- the distinct meanings of missing, `null`, empty, unknown, and deleted;
- ordering, delivery, freshness, and update guarantees;
- invalid-record, retention, sensitivity, and reprocessing rules.

Translate vocabulary, identity, units, absence, and lifecycle where meanings differ. Retain raw input only when audit or reprocessing justifies its privacy and storage cost.

## Model absence deliberately

- Use optional or nullable only when absence has one legitimate meaning in that contract; name it.
- Use distinct states for not loaded, unknown, inapplicable, redacted, deleted, Expected failure, and missing required data rather than one sentinel.
- At update boundaries, translate omission ("keep") and explicit `null` ("clear") once into domain commands. Keep load state in query or view models.
- Treat narrowing and defaults as decisions. Give them one Owner and apply a default only when it is truthful policy.

## Define the target model

Work in this order:

1. State the real-world fact, invariant, and counterexamples.
2. Define stable identity, Owner, lifecycle, deletion, retention, and historical meaning.
3. Define relationships and cardinality, including absence and ownership.
4. Define valid states, transitions, invalid combinations, and concurrency behavior.
5. Record volume, mutation rate, and access patterns; then choose persistence and indexes.
6. Derive API and UI projections last.

Do not force domain objects, rows, wire DTOs, and view models to be one-to-one. Prefer a small explicit structure; add accessors, repositories, or inheritance only to enforce an invariant, isolate a real boundary, or serve current interchangeable implementations.

## Normalize Canonical relational data where it helps

For Canonical transactional relational data, use third normal form as a check:

- give each row a stable key;
- store one fact once under the key that owns it;
- make non-key attributes depend on that key rather than another non-key attribute;
- represent relationships and cardinality explicitly;
- enforce uniqueness, references, required values, and valid ranges in the database when practical.

Normalize only until each fact has one writable home and the applicable invariants are enforceable. Do not decompose an atomic document, event, or historical snapshot merely to imitate a relational schema. Preserve domain atomicity and history first.

## Derive or denormalize only with proof

Prefer a view, materialized view, read model, or cache before duplicating facts in the Canonical write model. Before denormalizing, answer:

- Which measured SLO, constraint, or access pattern requires it?
- Which Canonical data produces it?
- How is it built, rebuilt, and invalidated, and what staleness is allowed?
- How is divergence detected and repaired?

If a required answer is missing, do not duplicate the Canonical fact. Measure the need and complete the design first.

## Verification

For each load-bearing Derived value, aggregate, or eligibility decision, list its inputs and invalidation events. Change each input in focused tests and observe the output; exercise expiry or revocation without an unrelated write. A declared input or event that cannot affect the result is not implemented.
