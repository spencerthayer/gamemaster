# ADR 0005: Separate extraction and import trust boundaries

## Status

Accepted

## Context

Source documents are untrusted text and may contain mistakes or prompt
injection. Model-assisted extraction is nondeterministic and can produce
malformed, unsupported, or policy-violating proposals. Allowing extraction to
write authoritative state directly would let model output bypass validation
and provenance rules.

## Decision

Separate extraction from import. Extraction produces untrusted proposal data.
It does not receive database authority. Import is deterministic: it validates
the proposal envelope and each proposed record, rejects records that violate
policy, and persists accepted records with parameterized SQL.

Imported facts retain provenance and begin as proposed, non-canon facts. A
separate promotion operation is required before they become confirmed canon.

## Consequences

Model output cannot directly establish campaign truth. Import behavior is
repeatable, testable, and auditable, and invalid proposals can be rejected
without discarding valid proposals from the same extraction.

The pipeline needs an explicit proposal format, validation errors, provenance
fields, and handling for partial acceptance. Extraction and import must evolve
their shared envelope carefully. Users also face an additional promotion step
before imported material becomes canon.

## Alternatives

- Let the extractor write facts directly. Rejected because nondeterministic,
  untrusted output would cross the persistence boundary unchecked.
- Trust all extracted facts but label their source. Rejected because
  provenance does not replace structural and policy validation.
- Require only deterministic extraction. Rejected because some source
  material needs model assistance, while the persistence boundary can still
  remain deterministic.
