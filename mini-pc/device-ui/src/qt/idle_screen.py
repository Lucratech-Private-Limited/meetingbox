"""PySide6 Idle screen — Figma ``#338:60`` baseline 1024×600 (see ``screens/idle.py``).

Layout uses managers only — no ``move()`` / ``setGeometry()`` for regions. Sizes and
offsets match the shipped Kivy reference (margins ~46/35/48/43, weather inset from
design top ``75``, clock / AMPM vertical relationship, recording card internals).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from qt.cover_image import CoverImageWidget
from qt.scaling import IDLE_FRAME, sp, spf, scale_from_window

_DEVICE_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _DEVICE_ROOT / "assets"
_IDLE = _ASSETS / "idle"

# Horizontal width reserved for HH:MM in Figma-ish layout (anchors AM/PM to the design column).
_DESIGN_CLOCK_NUM_W = 290


def _png(name: str) -> str | None:
    p = _IDLE / name
    return str(p) if p.is_file() else None


class IdleScreen(QWidget):
    """Fullscreen idle / lock-style surface."""

    def __init__(self) -> None:
        super().__init__()
        self._scale = 1.0
        self._meet_body = "No meetings today"

        stack_host = QWidget(self)
        slo = QStackedLayout(stack_host)
        slo.setStackingMode(QStackedLayout.StackingMode.StackAll)
        slo.setContentsMargins(0, 0, 0, 0)

        self._bg = CoverImageWidget(_png("background_landscape.png"), fallback_hex="#010c25")
        slo.addWidget(self._bg)

        self._pane = QWidget()
        self._pane.setAttribute(Qt.WA_TranslucentBackground, True)
        self._pane.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        slo.addWidget(self._pane)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(stack_host)

        lay = QVBoxLayout(self._pane)
        lay.setSpacing(0)
        self._main = lay

        top = QHBoxLayout()
        top.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(0)
        self._left_col = left

        self._greeting = QLabel()
        self._greeting.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        clock_row = QHBoxLayout()
        clock_row.setSpacing(0)
        clock_row.setContentsMargins(0, 0, 0, 0)
        self._clock_row = clock_row

        self._time = QLabel()
        self._time.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        amp_wrap = QVBoxLayout()
        amp_wrap.setSpacing(0)
        amp_wrap.setContentsMargins(0, 0, 0, 0)
        self._ampm_spacer_above = QWidget()
        self._ampm = QLabel()

        self._ampm_spacer_below = QWidget()
        amp_wrap.addWidget(self._ampm_spacer_above, 0)
        amp_wrap.addWidget(self._ampm, 0, Qt.AlignmentFlag.AlignLeft)
        amp_wrap.addWidget(self._ampm_spacer_below, 0)

        self._ampm_host = QWidget()
        self._ampm_host.setLayout(amp_wrap)

        clock_row.addWidget(self._time, 0)
        clock_row.addWidget(self._ampm_host, 1)

        self._date_sep = QWidget()
        self._date_lbl = QLabel()
        self._date_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self._clock_wrap = QWidget()
        self._clock_wrap.setLayout(clock_row)

        left.addWidget(self._greeting)
        left.addWidget(self._clock_wrap)
        left.addWidget(self._date_sep)
        left.addWidget(self._date_lbl)

        nw = QWidget()
        nw.setLayout(left)
        nw.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        top.addWidget(nw, stretch=1)

        wx_col = QVBoxLayout()
        wx_col.setSpacing(0)
        self._wx_top_pad = QWidget()
        wx_row = QHBoxLayout()
        wx_row.setSpacing(0)
        wx_row.setContentsMargins(0, 0, 0, 0)

        self._wx_ic = QLabel()
        self._temp = QLabel()
        self._cond = QLabel()
        self._temp.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self._cond.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        wx_txt_layout = QVBoxLayout()
        wx_txt_layout.setSpacing(0)
        wx_txt_layout.setContentsMargins(0, 0, 0, 0)
        self._wx_txt_layout = wx_txt_layout
        wx_txt_layout.addWidget(self._temp, 0)
        wx_txt_layout.addWidget(self._cond, 0)

        wx_row.addWidget(
            self._wx_ic,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self._wx_gap_widget = QWidget()
        wx_row.addWidget(self._wx_gap_widget, 0)
        wx_row.addLayout(wx_txt_layout, 0)

        wx_col.addWidget(self._wx_top_pad, 0)
        wx_wrap = QWidget()
        wx_wrap.setLayout(wx_row)
        wx_col.addWidget(wx_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        ww = QWidget()
        ww.setLayout(wx_col)
        ww.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        top.addWidget(ww, stretch=0, alignment=Qt.AlignmentFlag.AlignTop)

        lay.addLayout(top)

        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(0)
        bottom.setAlignment(Qt.AlignmentFlag.AlignBottom)

        next_col = QVBoxLayout()
        next_col.setSpacing(0)

        self._next_lbl = QLabel("Next up")
        self._next_sep1 = QWidget()

        self._cal = QLabel()
        self._meet_row_gap = QWidget()
        self._meet_time = QLabel("--:-- --")

        nrow = QHBoxLayout()
        nrow.setSpacing(0)
        nrow.addWidget(self._cal, 0, Qt.AlignmentFlag.AlignVCenter)
        nrow.addWidget(self._meet_row_gap, 0, Qt.AlignmentFlag.AlignVCenter)
        nrow.addWidget(self._meet_time, 0, Qt.AlignmentFlag.AlignVCenter)

        self._meet_row_host = QWidget()
        self._meet_row_host.setLayout(nrow)

        self._meet_title_sep = QWidget()
        self._meet_title = QLabel()
        self._meet_title.setWordWrap(False)
        self._more_sep = QWidget()

        self._more_lbl = QLabel()

        next_col.addWidget(self._next_lbl, 0)
        next_col.addWidget(self._next_sep1, 0)
        next_col.addWidget(self._meet_row_host, 0)
        next_col.addWidget(self._meet_title_sep, 0)
        next_col.addWidget(self._meet_title)
        next_col.addWidget(self._more_sep, 0)
        next_col.addWidget(self._more_lbl)

        nw2 = QWidget()
        nw2.setLayout(next_col)
        nw2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        bottom.addWidget(nw2, stretch=1, alignment=Qt.AlignmentFlag.AlignBottom)

        self._bottom_h_gap = QWidget()
        bottom.addWidget(self._bottom_h_gap, 0)

        self._rec_card = QWidget()
        self._rec_card.setObjectName("IdleRecCard")
        r_in = QHBoxLayout(self._rec_card)
        r_in.setSpacing(0)
        self._mic = QLabel()

        self._mic_holder = QWidget()
        mic_holder_lay = QVBoxLayout(self._mic_holder)
        mic_holder_lay.setContentsMargins(0, 0, 0, 0)
        mic_holder_lay.setSpacing(0)
        mic_holder_lay.addStretch(1)
        mic_holder_lay.addWidget(
            self._mic,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        mic_holder_lay.addStretch(1)

        r_txt = QVBoxLayout()
        r_txt.setSpacing(0)
        r_txt.setContentsMargins(0, 0, 0, 0)
        self._r_txt = r_txt
        self._cta1 = QLabel("Start Recording")
        self._cta2 = QLabel('Tap or say "start recording"')

        txt_pad_above = QWidget()
        txt_pad_below = QWidget()
        r_txt.addWidget(txt_pad_above, 1)
        r_txt.addWidget(self._cta1, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        r_txt.addWidget(self._cta2, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        r_txt.addWidget(txt_pad_below, 1)

        r_in.addWidget(self._mic_holder, 0)
        self._rec_gap_mic_text = QWidget()
        r_in.addWidget(self._rec_gap_mic_text, 0)
        r_in.addLayout(r_txt, 1)

        bottom.addWidget(self._rec_card, stretch=0, alignment=Qt.AlignmentFlag.AlignBottom)

        lay.addLayout(bottom)

        self._txt_pad_above = txt_pad_above
        self._txt_pad_below = txt_pad_below

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self._apply_demo_texts()
        self._tick()

    def _wf(self, px: float) -> QFont:
        fz = max(10, int(round(px)))
        f = QFont()
        f.setPixelSize(fz)
        f.setBold(True)
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        return f

    def _wf_plain(self, px: float) -> QFont:
        fz = max(10, int(round(px)))
        f = QFont()
        f.setPixelSize(fz)
        f.setBold(False)
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        return f

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self._scale = scale_from_window(w, h, IDLE_FRAME)
        s = self._scale

        ml, mt, mr, mb = sp(46, s), sp(35, s), sp(48, s), sp(43, s)
        self._main.setContentsMargins(ml, mt, mr, mb)

        cw = sp(_DESIGN_CLOCK_NUM_W, s)
        self._time.setFixedWidth(max(80, cw))

        self._greeting.setFont(self._wf(spf(20, s)))
        self._time.setFont(self._wf(spf(100, s)))
        self._ampm.setFont(self._wf(spf(35, s)))
        self._date_lbl.setFont(self._wf(spf(30, s)))
        self._temp.setFont(self._wf(spf(35, s)))
        self._cond.setFont(self._wf_plain(spf(28, s)))
        self._next_lbl.setFont(self._wf(spf(28, s)))
        self._meet_time.setFont(self._wf(spf(28, s)))
        self._meet_title.setFont(self._wf(spf(28, s)))
        self._more_lbl.setFont(self._wf(spf(28, s)))
        self._cta1.setFont(self._wf(spf(30, s)))
        self._cta2.setFont(self._wf_plain(spf(18, s)))

        clk_h = sp(120, s)
        self._time.setFixedHeight(clk_h)

        apm_h = max(20, round(self._ampm.fontMetrics().height()))
        ah = min(sp(66, s), max(1, clk_h - apm_h - 1))
        self._ampm_spacer_above.setFixedHeight(ah)
        self._ampm_spacer_below.setFixedHeight(max(1, clk_h - ah - apm_h))
        self._ampm_host.setMinimumHeight(clk_h)

        greet_h = max(sp(26, s), min(sp(38, s), round(self._greeting.fontMetrics().capHeight()) + sp(14, s)))
        self._greeting.setFixedHeight(greet_h)
        drift = greet_h - sp(20, s)
        self._clock_wrap.setStyleSheet(
            f"margin-top: -{max(0, drift)}px; background: transparent;",
        )

        dt_gap = max(0, sp(178 - (55 + 120), s))
        self._date_lbl.setFixedHeight(sp(44, s))
        self._date_sep.setFixedHeight(dt_gap)

        self._temp.setMinimumWidth(sp(168, s))
        self._temp.setMinimumHeight(sp(40, s))
        self._cond.setMinimumHeight(sp(36, s))

        self._wx_gap_widget.setFixedWidth(sp(29, s))
        self._wx_top_pad.setFixedHeight(max(1, sp(75 - 35, s)))
        self._wx_txt_layout.setSpacing(max(0, sp(122 - 75 - 40, s)))

        ir = _png("icon_sun.png")
        if ir:
            self._wx_ic.setPixmap(
                QPixmap(ir).scaledToHeight(
                    sp(64, s),
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        ic = _png("icon_calendar.png")
        if ic:
            self._cal.setPixmap(
                QPixmap(ic).scaledToHeight(
                    sp(34, s),
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )

        self._cal.setFixedSize(sp(34, s), sp(34, s))
        self._next_lbl.setFixedHeight(sp(36, s))
        self._meet_row_gap.setFixedWidth(sp(99 - 46 - 34, s))
        self._meet_row_host.setFixedHeight(sp(38, s))

        self._next_sep1.setFixedHeight(max(sp(14, s), sp(397 - (333 + 36), s)))
        self._meet_title_sep.setFixedHeight(max(sp(11, s), sp(446 - (397 + 34), s)))
        self._more_sep.setFixedHeight(max(sp(10, s), sp(497 - (446 + 28), s)))

        im = _png("mic_orb.png")
        side = sp(101, s)
        if im:
            self._mic.setPixmap(
                QPixmap(im).scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
            self._mic.setFixedWidth(side)
        self._mic_holder.setFixedWidth(side)

        self._bottom_h_gap.setFixedWidth(max(0, sp(427 - (46 + 370), s)))

        self._rec_card.setFixedSize(sp(414, s), sp(167, s))

        self._rec_gap_mic_text.setFixedWidth(sp(20, s))

        r_in_layout = self._rec_card.layout()
        rm_v = max(sp(12, s), sp(36, s) // 2)
        if isinstance(r_in_layout, QHBoxLayout):
            r_in_layout.setContentsMargins(sp(27, s), rm_v, sp(36, s), rm_v)

        self._r_txt.setSpacing(max(0, sp(4, s)))
        mh = (
            self._cta1.fontMetrics().height()
            + self._cta2.fontMetrics().height()
            + self._r_txt.spacing()
        )
        inner = sp(167, s) - 2 * rm_v
        slack = max(8, inner - mh)
        v_each = slack // 2
        self._txt_pad_above.setFixedHeight(v_each)
        self._txt_pad_below.setFixedHeight(max(8, slack - v_each))

        self._meet_title.setMaximumWidth(sp(370, s))

        bw = max(2, round(3 * s))
        br = max(10, sp(30, s))
        self._rec_card.setStyleSheet(
            f"""
            QWidget#IdleRecCard {{
              background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #0038b6, stop:1 #002376);
              border: {bw}px solid #034EE2;
              border-radius: {br}px;
            }}
            """
        )

        self._greeting.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._time.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._ampm.setStyleSheet("color: #b6baf2; margin: 0px; padding: 0px; background: transparent;")
        self._date_lbl.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._temp.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._cond.setStyleSheet("color: #b6baf2; margin: 0px; padding: 0px; background: transparent;")
        self._next_lbl.setStyleSheet("color: #006bf9; margin: 0px; padding: 0px; background: transparent;")
        self._meet_time.setStyleSheet("color: #006bf9; margin: 0px; padding: 0px; background: transparent;")
        self._meet_title.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._more_lbl.setStyleSheet("color: #006bf9; margin: 0px; padding: 0px; background: transparent;")
        self._cta1.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")
        self._cta2.setStyleSheet("color: #ffffff; margin: 0px; padding: 0px; background: transparent;")

        self._elide_meeting_title()

    def _elide_meeting_title(self) -> None:
        full = self._meet_body
        self._meet_title.setToolTip(full)
        fm = self._meet_title.fontMetrics()
        tw = min(self._meet_title.width(), self._meet_title.maximumWidth())
        if tw <= 0:
            tw = max(1, min(self.width() - sp(400, self._scale), self._meet_title.maximumWidth()))
        self._meet_title.setText(fm.elidedText(full, Qt.TextElideMode.ElideRight, tw))

    def _tick(self) -> None:
        n = datetime.now()
        self._time.setText(n.strftime("%I:%M").lstrip("0") or "12:00")
        self._ampm.setText(n.strftime("%p"))
        self._date_lbl.setText(n.strftime("%A, %B ") + str(n.day))
        self._elide_meeting_title()

    def _apply_demo_texts(self) -> None:
        self._greeting.setText("Good afternoon")
        self._temp.setText("35°C")
        self._cond.setText("Partly cloudy")
        self._meet_time.setText("10:00 AM")
        self._meet_body = (
            "One Piece UI Development Discussion — planning session for kiosk surfaces"
        )
        self._more_lbl.setText("+2 more")
        self._meet_title.setText(self._meet_body)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._elide_meeting_title()

    def set_weather(self, temp: str, cond: str) -> None:
        self._temp.setText(temp)
        self._cond.setText(cond)

    def set_next_meeting(self, time_s: str, title: str, more: str = "") -> None:
        self._meet_time.setText(time_s)
        self._meet_body = title
        self._more_lbl.setText(more)
        self._meet_title.setText(self._meet_body)
        self._elide_meeting_title()
