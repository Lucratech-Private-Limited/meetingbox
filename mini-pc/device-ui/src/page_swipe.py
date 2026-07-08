"""Interactive, finger-tracking horizontal page reveal — iOS/iPadOS Home-Screen
paging feel for Kivy.

Kivy's :class:`~kivy.uix.screenmanager.ScreenManager` transitions are *triggered*
animations: they cannot follow the user's finger. :class:`PageSwipeController`
fills that gap. It borrows an adjacent (non-current) screen straight out of the
ScreenManager, paints it on top of the live screen, and translates **both** pages
together so the interface feels physically attached to the finger:

    finger moves  → page moves
    finger pauses → page pauses
    finger reverses → page reverses

On release it settles with a critically-damped spring (no abrupt snapping):
past the completion threshold (or a quick flick) it commits to the destination
screen; otherwise it springs back to the origin.

The controller is direction-agnostic so the same class powers both the forward
reveal (Home → Start-Recording) and the reverse reveal (Start-Recording → Home):

    direction = +1  destination enters from the LEFT  as the finger moves RIGHT
    direction = -1  destination enters from the RIGHT as the finger moves LEFT

Host screens must expose ``set_page_offset(dx_px)`` (a transform-only translate,
see the screens' ``_ensure_page_translate`` helper) and live inside the app's
``screen_manager``.
"""

from __future__ import annotations

import logging
from typing import Callable

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.modalview import ModalView

logger = logging.getLogger(__name__)


def any_modal_open() -> bool:
    """True when a Kivy ``ModalView`` (edit / date-picker / etc.) is open.

    Used by screens to suppress page swipes while a modal owns the interaction.
    """
    try:
        return any(isinstance(w, ModalView) for w in Window.children)
    except Exception:
        return False

# ── Spring (critically damped) ────────────────────────────────────────────────
# Tuned for a calm, premium settle: quick to respond, no perceptible bounce.
_SPRING_K = 260.0
_SPRING_C = 2.0 * (_SPRING_K ** 0.5)   # critical damping
_SETTLE_POS_EPS = 0.0012
_SETTLE_VEL_EPS = 0.02

# Gesture recognition / commit heuristics (progress is 0..1 of one page width).
_ARM_PX = 16.0           # finger travel before a swipe is recognised
_H_DOMINANCE = 1.15      # horizontal must beat vertical by this factor
_V_ABORT_PX = 26.0       # vertical travel that abandons a pending swipe
_COMMIT_PROGRESS = 0.32  # past this fraction → settle onto destination
_FLICK_VEL = 0.95        # p-units/sec flick that commits regardless of progress
_REVERSE_VEL = 0.95      # p-units/sec reverse flick that cancels regardless


class PageSwipeController:
    """Drive one interactive page reveal for ``host``.

    Parameters
    ----------
    host:
        The screen the gesture starts on (already ``current`` in the manager).
        Must implement ``set_page_offset(dx)`` and expose ``.app`` + ``.width``.
    dest_name:
        Name of the adjacent screen to reveal/commit to.
    direction:
        ``+1`` reveal-from-left (finger →), ``-1`` reveal-from-right (finger ←).
    prepare_dest:
        Called with the borrowed destination screen just before it is shown, so
        the caller can put it into the correct visual state (e.g. READY).
    commit:
        Called to actually switch the ScreenManager once the reveal completes.
    can_start:
        Optional predicate; the gesture only arms when it returns ``True``.
    """

    def __init__(
        self,
        host,
        dest_name: str,
        *,
        direction: int,
        prepare_dest: Callable[[object], None],
        commit: Callable[[], None],
        can_start: Callable[[], bool] | None = None,
    ) -> None:
        self.host = host
        self.dest_name = dest_name
        self.direction = 1 if direction >= 0 else -1
        self.prepare_dest = prepare_dest
        self.commit = commit
        self.can_start = can_start

        self._touch = None
        self._armed = False
        self._active = False
        self._committing = False
        self._start_x = 0.0
        self._start_y = 0.0
        self._last_x = 0.0
        self._last_t = 0.0
        self._vel = 0.0          # gesture velocity, p-units/sec
        self._p = 0.0            # current progress 0..1
        self._dest = None        # borrowed destination screen
        self._spring_ev = None
        self._spring_v = 0.0
        self._spring_target = 0.0

    # ── geometry ──────────────────────────────────────────────────────────────
    @property
    def width(self) -> float:
        w = float(getattr(self.host, "width", 0) or 0) or float(Window.width or 0)
        return w if w > 1.0 else 1.0

    @property
    def is_engaged(self) -> bool:
        return self._active or self._committing or self._spring_ev is not None

    # ── touch handling (call these from the host screen) ──────────────────────
    def on_touch_down(self, touch) -> bool:
        # Never interrupt an in-flight settle/commit.
        if self._committing or self._spring_ev is not None:
            return False
        if self.can_start is not None and not self.can_start():
            return False
        self._touch = touch
        self._armed = True
        self._active = False
        self._start_x = touch.x
        self._start_y = touch.y
        self._last_x = touch.x
        self._last_t = Clock.get_time()
        self._vel = 0.0
        # Do not consume — let taps (mic orb, etc.) work normally until this
        # clearly becomes a horizontal drag.
        return False

    def on_touch_move(self, touch) -> bool:
        if touch is not self._touch:
            return False
        dx = touch.x - self._start_x
        dy = touch.y - self._start_y

        if not self._active:
            if not self._armed:
                return False
            signed = self.direction * dx
            if abs(dy) > _V_ABORT_PX and abs(dy) > abs(dx):
                # Predominantly vertical → not our gesture.
                self._armed = False
                return False
            if signed > _ARM_PX and abs(dx) > abs(dy) * _H_DOMINANCE:
                if not self._begin():
                    self._armed = False
                    return False
            else:
                return False

        # Active: track the finger.
        signed = self.direction * dx
        p = max(0.0, min(1.0, signed / self.width))
        now = Clock.get_time()
        if now > self._last_t:
            self._vel = (self.direction * (touch.x - self._last_x) / self.width) / (now - self._last_t)
            self._last_x = touch.x
            self._last_t = now
        self._set_progress(p)
        return True

    def on_touch_up(self, touch) -> bool:
        if touch is not self._touch:
            return False
        self._touch = None
        if not self._active:
            self._armed = False
            return False
        # Decide: commit onto destination, or spring back to origin.
        if self._vel <= -_REVERSE_VEL:
            target = 0.0
        elif self._vel >= _FLICK_VEL or self._p >= _COMMIT_PROGRESS:
            target = 1.0
        else:
            target = 0.0
        self._settle(target)
        return True

    # ── internals ─────────────────────────────────────────────────────────────
    def _begin(self) -> bool:
        sm = getattr(self.host, "app", None)
        sm = getattr(sm, "screen_manager", None)
        if sm is None:
            return False
        try:
            dest = sm.get_screen(self.dest_name)
        except Exception:
            logger.exception("PageSwipe: destination screen %r missing", self.dest_name)
            return False
        if dest is self.host or dest.parent is not None:
            # Cannot borrow a screen that is current / already parented.
            return False
        self._dest = dest
        try:
            self.prepare_dest(dest)
        except Exception:
            logger.exception("PageSwipe: prepare_dest failed")
        dest.size_hint = (None, None)
        dest.size = Window.size
        dest.pos = (0, 0)
        if hasattr(dest, "set_page_offset"):
            dest.set_page_offset(self.direction * -self.width)
        Window.add_widget(dest)
        self._active = True
        self._armed = False
        self._p = 0.0
        self._set_progress(0.0)
        return True

    def _set_progress(self, p: float) -> None:
        self._p = p
        w = self.width
        if hasattr(self.host, "set_page_offset"):
            self.host.set_page_offset(self.direction * p * w)
        if self._dest is not None and hasattr(self._dest, "set_page_offset"):
            self._dest.set_page_offset(self.direction * (p - 1.0) * w)

    def _settle(self, target: float) -> None:
        if self._spring_ev is not None:
            self._spring_ev.cancel()
            self._spring_ev = None
        self._spring_target = target
        self._spring_v = self._vel
        self._spring_ev = Clock.schedule_interval(self._spring_step, 0)

    def _spring_step(self, dt: float) -> bool:
        dt = min(max(float(dt), 0.0), 1.0 / 30.0)
        if dt <= 0.0:
            return True
        target = self._spring_target
        x = self._p - target
        a = -_SPRING_K * x - _SPRING_C * self._spring_v
        self._spring_v += a * dt
        p = self._p + self._spring_v * dt
        # Allow a hair of overshoot, but never run off the rails.
        if p < -0.02:
            p, self._spring_v = -0.02, 0.0
        elif p > 1.02:
            p, self._spring_v = 1.02, 0.0
        self._set_progress(max(0.0, min(1.0, p)))
        self._p = p
        if abs(p - target) < _SETTLE_POS_EPS and abs(self._spring_v) < _SETTLE_VEL_EPS:
            self._set_progress(target)
            self._finish_settle()
            return False
        return True

    def _finish_settle(self) -> None:
        if self._spring_ev is not None:
            self._spring_ev.cancel()
            self._spring_ev = None
        if self._spring_target >= 0.999:
            self._do_commit()
        else:
            self._do_cancel()

    def _release_dest(self) -> None:
        dest = self._dest
        self._dest = None
        if dest is None:
            return
        if dest.parent is not None:
            dest.parent.remove_widget(dest)
        if hasattr(dest, "set_page_offset"):
            dest.set_page_offset(0.0)
        dest.size_hint = (1, 1)

    def _do_cancel(self) -> None:
        self._release_dest()
        if hasattr(self.host, "set_page_offset"):
            self.host.set_page_offset(0.0)
        self._active = False
        self._armed = False
        self._p = 0.0

    def _do_commit(self) -> None:
        self._committing = True
        self._active = False
        self._armed = False
        # Hand the borrowed screen back so the ScreenManager can adopt it, then
        # switch instantly (NoTransition). Because the borrowed widget *is* the
        # destination screen, the swap is visually identical → no flicker.
        self._release_dest()
        try:
            self.commit()
        except Exception:
            logger.exception("PageSwipe: commit failed")
        if hasattr(self.host, "set_page_offset"):
            self.host.set_page_offset(0.0)
        self._committing = False
        self._p = 0.0

    # ── housekeeping ──────────────────────────────────────────────────────────
    def cancel(self) -> None:
        """Abort any in-flight gesture/settle and restore resting state."""
        if self._spring_ev is not None:
            self._spring_ev.cancel()
            self._spring_ev = None
        self._touch = None
        if self._active or self._dest is not None:
            self._do_cancel()
        self._committing = False
