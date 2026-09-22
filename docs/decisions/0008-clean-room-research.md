# ADR 0008: Clean-room research policy

## Status

Accepted

## Context

Prior tabletop projects can reveal useful problem shapes and architectural
tradeoffs. Their licenses vary, and some research repositories do not state a
license. Copying source code, schemas, prompts, or other protected expression
would create provenance and license risk for this repository.

## Decision

Use research repositories only to study behavior, constraints, and general
architectural ideas. Keep research clones outside version control under the
git-ignored `research/` path. Record findings and independent design
conclusions in `docs/research/`.

Implement all production code and project documentation as clean-room work.
Do not copy code from research clones. No code was copied from the research
clones used for this project. Treat a repository with no stated license as
unavailable for code reuse.

## Consequences

The project has a clear provenance rule and can learn from prior art without
mixing external source into the implementation. Reviewers can inspect the
research notes and the repository history separately.

Clean-room implementation takes more time and can reproduce work that a
compatible license might otherwise permit reusing. Researchers must separate
observed behavior and ideas from protected implementation details, maintain
license notes, and avoid committing clones or source artifacts.

## Alternatives

- Copy code when it appears useful. Rejected because appearance does not
  establish license permission or acceptable provenance.
- Reuse code only from repositories with permissive licenses. Rejected as the
  default policy because this project chose independent implementation across
  all research inputs; a future exception would need explicit review.
- Avoid prior-art research entirely. Rejected because behavioral comparisons
  still help expose requirements and architectural risks.
