"""Residual cleanup helper for the legacy card-minimize transform.

The card fly-away animation has been replaced by the premium genie in
``components/email_genie.py`` (``play_genie``). The only thing kept here is
:func:`_cleanup_minimize`, which the creation screens still call defensively in
their ``restore_action_visuals`` to strip any leftover transform instructions.
With the old ``minimize_card`` gone it is effectively a no-op, but it keeps the
screens' restore path safe and idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _cleanup_minimize(card) -> None:
    """Remove any ``PushMatrix``/``Scale``/``PopMatrix`` left on *card* by a
    previous minimize transform. Safe to call when nothing is present."""
    instrs = getattr(card, "_minimize_instrs", None)
    if not instrs:
        return
    push, scale, pop = instrs
    for grp, ins in ((card.canvas.before, push), (card.canvas.before, scale),
                     (card.canvas.after, pop)):
        try:
            grp.remove(ins)
        except Exception:
            pass
    card._minimize_instrs = None
