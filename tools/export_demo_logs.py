"""Export demo evidence from logs

Status: NOT IMPLEMENTED. Phase 8 deliverable.

This file exists so the repository structure matches plan.md section 4 during
Phase 0. It intentionally contains no behaviour. Implement it in Phase 8;
until then, calling any entry point raises NotImplementedError.

See plan.md section 11 (Phase 8) for the acceptance criteria this module
must satisfy.
"""

from __future__ import annotations

PHASE = 8


def _not_implemented(name: str) -> "NotImplementedError":
    return NotImplementedError(
        "{} is a Phase {} deliverable (plan.md section 11) and is not "
        "implemented yet. Phase 0 only establishes the repository structure."
        .format(name, PHASE)
    )


__all__: list[str] = []
