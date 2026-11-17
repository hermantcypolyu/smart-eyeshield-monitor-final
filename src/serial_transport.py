"""Serial link, handshake, retry, reconnect

Status: NOT IMPLEMENTED. Phase 4 deliverable.

This file exists so the repository structure matches plan.md section 4 during
Phase 0. It intentionally contains no behaviour. Implement it in Phase 4;
until then, calling any entry point raises NotImplementedError.

See plan.md section 11 (Phase 4) for the acceptance criteria this module
must satisfy.
"""

from __future__ import annotations

PHASE = 4


def _not_implemented(name: str) -> "NotImplementedError":
    return NotImplementedError(
        "{} is a Phase {} deliverable (plan.md section 11) and is not "
        "implemented yet. Phase 0 only establishes the repository structure."
        .format(name, PHASE)
    )


__all__: list[str] = []
