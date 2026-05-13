"""PySide6 dashboard home — boxed layouts + design-relative scaling (:data:`HOME_FRAME`).

Matches the Kivy home screen hierarchy: header row → three-column body → three
bottom chips → “Try saying” footer.  No ``move()`` for structure.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from qt.scaling import HOME_FRAME, sp, spf, scale_from_window


class HomeScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._scale = 1.0

        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(0)

        # Header
        hdr = QHBoxLayout()
        self._greet = QLabel("Good morning")
        self._listen = QLabel("Listening")
        self._listen.setObjectName("pill_listen")
        self._listen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self._greet, stretch=1)
        hdr.addWidget(self._listen, stretch=0)

        # Body row
        body = QHBoxLayout()
        self._hero = self._glass_card("Hero placeholder")
        self._sum = self._glass_card("Last Meeting Summary")
        self._brief = self._glass_card("Morning Brief")
        body.addWidget(self._hero, stretch=48)
        body.addWidget(self._sum, stretch=26)
        body.addWidget(self._brief, stretch=26)

        # Bottom strip
        bot = QHBoxLayout()
        self._c1 = self._mini_card("Schedule")
        self._c2 = self._mini_card("Email")
        self._c3 = self._mini_card("Tasks")
        bot.addWidget(self._c1, stretch=43)
        bot.addWidget(self._c2, stretch=285)
        bot.addWidget(self._c3, stretch=285)

        # Say row
        self._say = QLabel("Try asking Tony")
        self._say.setWordWrap(False)
        self._say.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._say.setObjectName("say_row")

        self._lay.addLayout(hdr, stretch=54)
        self._lay.addLayout(body, stretch=263)
        self._lay.addLayout(bot, stretch=102)
        self._lay.addWidget(self._say, stretch=71)

        for w in (self._hero, self._sum, self._brief, self._c1, self._c2, self._c3):
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _glass_card(self, title: str) -> QWidget:
        w = QWidget()
        w.setObjectName("glass")
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        t = QLabel(title)
        t.setWordWrap(True)
        v.addWidget(t)
        return w

    def _mini_card(self, text: str) -> QWidget:
        w = QWidget()
        w.setObjectName("mini")
        l = QVBoxLayout(w)
        l.addWidget(QLabel(text, alignment=Qt.AlignmentFlag.AlignCenter))
        return w

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self._scale = scale_from_window(w, h, HOME_FRAME)
        s = self._scale

        ml, mt, mr, mb = sp(17, s), sp(15, s), sp(17, s), sp(8, s)
        self._lay.setContentsMargins(ml, mt, mr, mb)

        def _px(d: float) -> QFont:
            f = QFont()
            f.setPixelSize(max(8, int(round(spf(d, s)))))
            return f

        self._greet.setFont(_px(30))
        self._listen.setFont(_px(20))
        self._say.setFont(_px(15))

        self.setStyleSheet(
            f"""
            QWidget {{ background: #01081a; color: #ffffff; }}
            QWidget#glass {{
              background: rgba(4,17,62,240);
              border: 1px solid #3f4253;
              border-radius: {sp(14, s)}px;
            }}
            QLabel#pill_listen {{
              color: white;
              background: rgba(17,43,149,240);
              border: 1px solid #3f4253;
              border-radius: {sp(28, s)}px;
              min-width: {sp(214, s)}px;
              min-height: {sp(54, s)}px;
            }}
            QWidget#mini {{
              background: rgba(4,17,62,240);
              border: 1px solid #3f4253;
              border-radius: {sp(12, s)}px;
            }}
            QLabel#say_row {{
              background: rgba(4,43,149,235);
              border: 1px solid #314060;
              border-radius: {sp(21, s)}px;
              padding: {sp(10, s)}px {sp(18, s)}px;
            }}
            """
        )
