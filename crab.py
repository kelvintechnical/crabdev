#!/usr/bin/env python3
"""
crabdev -- Maryland Blue Crab Desktop Overlay
MIT License -- Free and Open Source
github.com/kelvintechnical/crabdev
"""

import sys
import random
import threading
import time
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget
from PySide6.QtCore import Qt, Signal, QObject, QSize, QTimer, QPoint
from PySide6.QtGui import QMovie, QFont, QPainter, QColor, QPen, QPainterPath

# --- Config ---
SCRIPT_DIR       = Path(__file__).parent
WATCHING_TIMEOUT = 2.0
WAITING_TIMEOUT  = 10.0
AFK_TIMEOUT      = 60.0
PANIC_DURATION   = 4.0
KEYBOARD_DEVICE  = "/dev/input/event2"
SPEED_FAST       = 5.0
SPEED_SLOW       = 1.0

IMAGES = {
    "idle":          SCRIPT_DIR / "idle.gif",
    "typing":        SCRIPT_DIR / "typing.gif",
    "waiting":       SCRIPT_DIR / "waiting.gif",
    "watching":      SCRIPT_DIR / "watching.gif",
    "panic":         SCRIPT_DIR / "panic.gif",
    "panic_message": SCRIPT_DIR / "panic_message.gif",
    "afk":           SCRIPT_DIR / "afk.gif",
}

WATCHING_QUIPS = [
    "Still here.",
    "My exoskeleton is aging.",
    "I have 8 legs and nothing to do.",
    "...any day now.",
    "I'm not NOT judging you.",
    "I've crabbed faster than this.",
    "The cursor blinks. You do not.",
]

WAITING_QUIPS = [
    "ok are you coming back or",
    "I could be at the beach right now.",
    "my coffee is cold.",
    "still waiting...",
    "zzzz...",
    "no rush. really.",
]

PANIC_QUIPS = [
    "not again.",
    "I went to school for this?",
    "have you tried turning it off",
    "sir this is a Wendy's",
    "my therapist warned me about you",
    "error detected. cope.",
    "I felt that in my exoskeleton",
    "bro.",
    "404: dignity not found",
    "we are so cooked",
]

FAST_QUIPS = [
    "ok ok ok ok ok",
    "we were cooking",
    "that was a vibe",
    "alright turbo",
]

SLOW_QUIPS = [
    "we have all day apparently",
    "one key at a time I guess",
    "no rush. really.",
    "zzzz...",
]

NON_TYPING_STATES = {"watching", "waiting", "afk", "panic", "panic_message"}


# --- Signals ---
class CrabSignals(QObject):
    state_changed  = Signal(str)
    quip_triggered = Signal(str)

signals = CrabSignals()


# --- Stats ---
class CrabStats:
    def __init__(self):
        self.total_keystrokes = 0
        self.session_start    = datetime.now()
        self.panic_count      = 0
        self.afk_count        = 0
        self._recent_keys     = []

    def record_keypress(self):
        self.total_keystrokes += 1
        now = time.time()
        self._recent_keys.append(now)
        self._recent_keys = [t for t in self._recent_keys if now - t <= 2.0]

    def check_speed_after_stop(self):
        kps = len(self._recent_keys) / 2.0
        if kps >= SPEED_FAST:
            signals.quip_triggered.emit(random.choice(FAST_QUIPS))
        elif kps <= SPEED_SLOW and self.total_keystrokes > 0:
            signals.quip_triggered.emit(random.choice(SLOW_QUIPS))

    def record_panic(self):
        self.panic_count += 1

    def record_afk(self):
        self.afk_count += 1

    def summary(self):
        elapsed = datetime.now() - self.session_start
        hours, rem = divmod(int(elapsed.total_seconds()), 3600)
        minutes, _ = divmod(rem, 60)
        return (
            f"Keystrokes: {self.total_keystrokes:,}\n"
            f"Panics: {self.panic_count}\n"
            f"AFK: {self.afk_count}x\n"
            f"Session: {hours}h {minutes}m\n"
            f"I judged every single one."
        )


# --- State Machine ---
class CrabState:
    def __init__(self, stats: CrabStats):
        self.current = "idle"
        self.lock    = threading.Lock()
        self.stats   = stats
        self._watching_timer = None
        self._waiting_timer  = None
        self._afk_timer      = None
        self._panic_timer    = None

    def set_typing(self):
        self.stats.record_keypress()
        with self.lock:
            self._cancel_all_timers()
            if self.current != "typing":
                self.current = "typing"
                signals.state_changed.emit("typing")
            self._watching_timer = threading.Timer(WATCHING_TIMEOUT, self._go_watching)
            self._watching_timer.daemon = True
            self._watching_timer.start()

    def set_panic(self):
        self.stats.record_panic()
        with self.lock:
            self._cancel_all_timers()
            self.current = "panic_message"
            signals.state_changed.emit("panic_message")
            signals.quip_triggered.emit(random.choice(PANIC_QUIPS))
            self._panic_timer = threading.Timer(PANIC_DURATION, self._panic_done)
            self._panic_timer.daemon = True
            self._panic_timer.start()

    def _go_watching(self):
        with self.lock:
            if self.current == "typing":
                self.current = "watching"
                signals.state_changed.emit("watching")
                self.stats.check_speed_after_stop()
                self._waiting_timer = threading.Timer(WAITING_TIMEOUT, self._go_waiting)
                self._waiting_timer.daemon = True
                self._waiting_timer.start()

    def _go_waiting(self):
        with self.lock:
            if self.current == "watching":
                self.current = "waiting"
                signals.state_changed.emit("waiting")
                signals.quip_triggered.emit(random.choice(WAITING_QUIPS))
                self._afk_timer = threading.Timer(AFK_TIMEOUT, self._go_afk)
                self._afk_timer.daemon = True
                self._afk_timer.start()

    def _go_afk(self):
        self.stats.record_afk()
        with self.lock:
            if self.current == "waiting":
                self.current = "afk"
                signals.state_changed.emit("afk")

    def _panic_done(self):
        with self.lock:
            if self.current == "panic_message":
                self.current = "watching"
                signals.state_changed.emit("watching")

    def _cancel_all_timers(self):
        for attr in ("_watching_timer", "_waiting_timer", "_afk_timer", "_panic_timer"):
            t = getattr(self, attr)
            if t:
                t.cancel()
            setattr(self, attr, None)


# --- Keyboard Listener ---
def keyboard_listener(state: CrabState):
    try:
        import evdev
        from evdev import ecodes
        dev = evdev.InputDevice(KEYBOARD_DEVICE)
        print(f"[crabdev] Listening on: {dev.name}")
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY and event.value == 1:
                state.set_typing()
    except PermissionError:
        print("[crabdev] Permission denied. Fix: sudo chmod a+r /dev/input/event2")
    except FileNotFoundError:
        print(f"[crabdev] Keyboard not found: {KEYBOARD_DEVICE}")
    except Exception as e:
        print(f"[crabdev] Keyboard error: {e}")


# --- Stderr Monitor ---
class StderrMonitor:
    def __init__(self, state: CrabState, original):
        self.state    = state
        self.original = original

    def write(self, text):
        self.original.write(text)
        if any(kw in text for kw in ("Traceback", "Error:", "Exception", "SyntaxError")):
            self.state.set_panic()

    def flush(self):
        self.original.flush()


# --- Speech Bubble ---
class SpeechBubble(QWidget):
    def __init__(self):
        super().__init__()
        self._text = ""
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(230, 75)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_quip(self, text, anchor_pos, duration=3500):
        self._text = text
        self.move(anchor_pos.x() - 20, anchor_pos.y() - 85)
        self.show()
        self.raise_()
        self.update()
        self._timer.start(duration)

    def paintEvent(self, event):
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(4, 4, 222, 58, 14, 14)
        painter.fillPath(path, QColor(255, 255, 255, 235))
        painter.setPen(QPen(QColor(60, 60, 60), 1.5))
        painter.drawPath(path)
        painter.setPen(QColor(25, 25, 25))
        font = QFont("monospace", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(12, 8, 206, 50,
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
            self._text)


# --- Main Overlay ---
class CrabOverlay(QLabel):
    def __init__(self, state: CrabState, stats: CrabStats):
        super().__init__()
        self.state       = state
        self.stats       = stats
        self.movies      = {}
        self._drag_start  = None
        self._drag_origin = None

        for name, path in IMAGES.items():
            if path.exists():
                movie = QMovie(str(path))
                movie.setScaledSize(QSize(220, 220))
                self.movies[name] = movie
            else:
                print(f"[crabdev] WARNING: Missing {path}")

        if not self.movies:
            print("[crabdev] ERROR: No GIFs found.")
            sys.exit(1)

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(220, 220)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 240, screen.height() - 260)

        self.bubble = SpeechBubble()

        signals.state_changed.connect(self.on_state_changed)
        signals.quip_triggered.connect(self.on_quip)

        self.set_state("idle")
        self.show()

    def set_state(self, name: str):
        if name not in self.movies:
            name = list(self.movies.keys())[0]
        self.setMovie(self.movies[name])
        self.movies[name].start()

    def on_state_changed(self, name: str):
        if name in ("typing", "idle"):
            self.bubble.hide()
        self.set_state(name)

    def on_quip(self, text: str):
        if self.state.current in NON_TYPING_STATES:
            self.bubble.show_quip(text, self.pos())

    # --- Drag (manual delta, works on Wayland) ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.windowHandle().startSystemMove()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 4px;
                font-family: monospace;
                font-size: 12px;
            }
            QMenu::item { padding: 4px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #313244; }
            QMenu::separator { background-color: #45475a; height: 1px; margin: 4px 0; }
        """)

        title = menu.addAction("🦀 crabdev v0.3.2")
        title.setEnabled(False)
        menu.addSeparator()
        stats_action = menu.addAction("📊 View Stats")
        menu.addSeparator()

        state_labels = {
            "idle":          "😴 Idle",
            "typing":        "⌨️  Typing",
            "waiting":       "👀 Waiting",
            "watching":      "😏 Watching",
            "panic_message": "😱 Panic",
            "afk":           "🚶 AFK",
        }

        for state_name, label in state_labels.items():
            if state_name in self.movies:
                action = menu.addAction(f"Preview: {label}")
                action.setData(state_name)

        menu.addSeparator()
        quit_action = menu.addAction("✖  Quit")

        chosen = menu.exec(event.globalPos())
        if chosen == quit_action:
            QApplication.quit()
        elif chosen == stats_action:
            self.bubble.setFixedSize(230, 130)
            self.bubble.show_quip(self.stats.summary(), self.pos(), duration=6000)
        elif chosen and chosen.data():
            self.set_state(chosen.data())


# --- Main ---
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    stats = CrabStats()
    state = CrabState(stats)
    sys.stderr = StderrMonitor(state, sys.stderr)

    kb_thread = threading.Thread(target=keyboard_listener, args=(state,), daemon=True)
    kb_thread.start()

    overlay = CrabOverlay(state, stats)

    print("[crabdev] 🦀 Crab is on duty.")
    print("[crabdev] idle -> typing -> watching(2s) -> waiting(10s) -> afk(60s)")
    print("[crabdev] Bubble never interrupts typing.")
    print("[crabdev] Right-click for stats, previews, quit.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
