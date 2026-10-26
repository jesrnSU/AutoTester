"""Carbon Design System — Gray 90 theme stylesheet for PySide6."""

# ── Gray 90 color tokens ──────────────────────────────────────────────────────
BACKGROUND = "#161616"  # Gray 100  — app/window background
LAYER_01 = "#262626"  # Gray 90   — cards, panels, inputs
LAYER_02 = "#393939"  # Gray 80   — elevated surfaces, hover
LAYER_03 = "#525252"  # Gray 70   — borders, dividers
INTERACTIVE = "#0f62fe"  # Blue 60   — primary action
INTERACTIVE_HOVER = "#0353e9"  # Blue 70
DANGER = "#da1e28"  # Red 60
TEXT_PRIMARY = "#f4f4f4"  # Gray 10
TEXT_SECONDARY = "#c6c6c6"  # Gray 30
TEXT_DISABLED = "#6f6f6f"  # Gray 50
FOCUS = "#ffffff"

STYLESHEET = f"""
/* ── Global ──────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT_PRIMARY};
    font-family: "Helvetica Neue", Arial;
    font-size: 14px;
}}

/* ── Labels ─────────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {TEXT_PRIMARY};
}}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {LAYER_02};
    color: {TEXT_PRIMARY};
    border: none;
    padding: 8px 16px;
    min-width: 80px;
    outline: none;
}}
QPushButton:hover {{
    background-color: {LAYER_03};
}}
QPushButton:pressed {{
    background-color: {INTERACTIVE};
}}
QPushButton:checked {{
    background-color: {INTERACTIVE};
    color: {FOCUS};
}}
QPushButton:disabled {{
    background-color: {LAYER_01};
    color: {TEXT_DISABLED};
}}

/* Primary button — apply with setProperty("primary", True) */
QPushButton[primary="true"] {{
    background-color: {INTERACTIVE};
    color: {FOCUS};
}}
QPushButton[primary="true"]:hover {{
    background-color: {INTERACTIVE_HOVER};
}}

/* Danger button */
QPushButton[danger="true"] {{
    background-color: {DANGER};
    color: {FOCUS};
}}

/* ── Inputs ──────────────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {LAYER_01};
    color: {TEXT_PRIMARY};
    border: none;
    border-bottom: 1px solid {LAYER_03};
    padding: 6px 8px;
    min-height: 32px;
    selection-background-color: {INTERACTIVE};
}}
QLineEdit:focus {{
    border-bottom: 2px solid {INTERACTIVE};
}}
QLineEdit:disabled {{
    color: {TEXT_DISABLED};
    border-bottom-color: {LAYER_02};
}}

/* ── ComboBox ────────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {LAYER_01};
    color: {TEXT_PRIMARY};
    border: none;
    border-bottom: 1px solid {LAYER_03};
    padding: 6px 8px;
    min-height: 32px;
}}
QComboBox:focus {{
    border-bottom: 2px solid {INTERACTIVE};
}}
QComboBox::drop-down {{
    border: none;
    width: 32px;
}}
QComboBox::down-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {TEXT_SECONDARY};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {LAYER_02};
    color: {TEXT_PRIMARY};
    selection-background-color: {LAYER_03};
    border: none;
    outline: none;
}}

/* ── Dialog ──────────────────────────────────────────────────────────────── */
QDialog {{
    background-color: {BACKGROUND};
}}

/* ── Scrollbars ──────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BACKGROUND};
    width: 6px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {LAYER_03};
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def apply(app) -> None:
    """Apply the Carbon Gray 90 stylesheet to a QApplication."""
    app.setStyleSheet(STYLESHEET)
