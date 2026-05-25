#!/usr/bin/env python3
"""
crabdev -- Maryland Blue Crab Desktop Overlay
A Zoidberg-inspired coding companion for developers.
MIT License -- Free and Open Source
github.com/kelvintechnical/crabdev
"""

import sys
import os
import threading
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QMenu
from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject, QSize
from PySide6.QtGui import QMovie, QCursor, QAction

# --- Config ---
SCRIPT_DIR = Path(__file__).parent
IMAGES = {
    "idle":     SCRIPT_DIR / "idle.gif",
    "typing":   SCRIPT_DIR / "typing.gif",
    "waiting":  SCRIPT_DIR / "waiting.gif",
    "watching": SCRIPT_DIR / "watching.gif",
    "panic":    SCRIPT_DIR / "panic.gif",
}
KEYBOARD_DEVICE  = "/dev/input/event2"
WATCHING_TIMEOUT = 2.0   # seconds: TYPING -> WATCHING
WAITING_TIMEOUT  = 10.0  # seconds: WATCHING -> WAITING
PANIC_DURATION   = 4.0   # seconds: PANIC -> WATCHING


# --- Signals bridge (evdev thread -> Qt main thread) ---
class CrabSignals(QObject):
    state_changed = Signal(str)

signals = CrabSignals()


# --- State Machine ---
class CrabState:
    def __init__(self):
        self.current = "idle"
        self.lock = threading.Lock()
        self._watching_timer = None
        self._waiting_timer  = None
        self._panic_timer    = None

    def set_typing(self):
        with self.lock:
            self._cancel_all_timers()
            if self.current != "typing":
                self.current = "typing"
                signals.state_changed.emit("typing")
            self._watching_timer = threading.Timer(WATCHING_TIMEOUT, self._go_watching)
            self._watching_timer.daemon = True
            self._watching_timer.start()

    def set_panic(self):
        with self.lock:
            self._cancel_all_timers()
            self.current = "panic"
            signals.state_changed.emit("panic")
            self._panic_timer = threading.Timer(PANIC_DURATION, self._panic_done)
            self._panic_timer.daemon = True
            self._panic_timer.start()

    def _go_watching(self):
        with self.lock:
            if self.current == "typing":
                self.current = "watching"
                signals.state_changed.emit("watching")
                self._waiting_timer = threading.Timer(WAITING_TIMEOUT, self._go_waiting)
                self._waiting_timer.daemon = True
                self._waiting_timer.start()

    def _go_waiting(self):
        with self.lock:
            if self.current == "watching":
                self.current = "waiting"
                signals.state_changed.emit("waiting")

    def _panic_done(self):
        with self.lock:
            if self.current == "panic":
                self.current = "watching"
                signals.state_changed.emit("watching")

    def _cancel_all_timers(self):
        for attr in ("_watching_timer", "_waiting_timer", "_panic_timer"):
            t = getattr(self, attr)
            if t:
                t.cancel()
            setattr(self, attr, None)


# --- Keyboard Listener (evdev, Wayland-safe) ---
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
        print("[crabdev] Permission denied on /dev/input/event2")
        print("[crabdev] Fix: sudo chmod a+r /dev/input/event2")
    except FileNotFoundError:
        print(f"[crabdev] Keyboard device not found: {KEYBOARD_DEVICE}")
    except Exception as e:
        print(f"[crabdev] Keyboard listener error: {e}")


# --- Stderr Monitor (panic on errors) ---
class StderrMonitor:
    def __init__(self, state: CrabState, original):
        self.state = state
        self.original = original

    def write(self, text):
        self.original.write(text)
        if any(kw in text for kw in ("Traceback", "Error:", "Exception", "SyntaxError")):
            self.state.set_panic()

    def flush(self):
        self.original.flush()


# --- Qt Overlay Window ---
class CrabOverlay(QLabel):
    def __init__(self, state: CrabState):
        super().__init__()
        self.state = state
        self.movies = {}
        self._drag_pos = None

        for name, path in IMAGES.items():
            if path.exists():
                movie = QMovie(str(path))
                movie.setScaledSize(QSize(220, 220))
                self.movies[name] = movie
            else:
                print(f"[crabdev] WARNING: Missing {path}")

        if not self.movies:
            print("[crabdev] ERROR: No GIF files found.")
            sys.exit(1)

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(220, 220)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 240, screen.height() - 260)

        signals.state_changed.connect(self.on_state_changed)
        self.set_state("idle")
        self.show()

    def set_state(self, name: str):
        if name not in self.movies:
            name = list(self.movies.keys())[0]
        movie = self.movies[name]
        self.setMovie(movie)
        movie.start()

    def on_state_changed(self, name: str):
        self.set_state(name)

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
                font-size: 13px;
            }
            QMenu::item { padding: 4px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #313244; }
            QMenu::separator { background-color: #313244; height: 1px; margin: 4px 0; }
        """)

        title = menu.addAction("🦀 crabdev v0.2.0")
        title.setEnabled(False)
        menu.addSeparator()

        state_labels = {
            "idle":     "😴 Preview: Idle",
            "typing":   "⌨️  Preview: Typing",
            "waiting":  "👀 Preview: Waiting",
            "watching": "😏 Preview: Watching",
            "panic":    "😱 Preview: Panic",
        }

        for state_name, label in state_labels.items():
            if state_name in self.movies:
                action = menu.addAction(label)
                action.setData(state_name)

        menu.addSeparator()
        quit_action = menu.addAction("✖  Quit")

        chosen = menu.exec(event.globalPos())
        if chosen == quit_action:
            QApplication.quit()
        elif chosen and chosen.data():
            self.set_state(chosen.data())


# --- Main ---
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    state = CrabState()
    sys.stderr = StderrMonitor(state, sys.stderr)

    kb_thread = threading.Thread(target=keyboard_listener, args=(state,), daemon=True)
    kb_thread.start()

    overlay = CrabOverlay(state)

    print("[crabdev] 🦀 Crab is on duty. Start typing!")
    print("[crabdev] idle -> typing -> watching (2s) -> waiting (10s)")
    print("[crabdev] Panic triggers on Traceback/Error in stderr")
    print("[crabdev] Right-click to preview states or quit.")
    print("[crabdev] Drag with left mouse to reposition.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
