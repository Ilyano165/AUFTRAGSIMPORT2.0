"""Design-Tokens und Stylesheet.

Ruhige, neutrale Windows-Oberfläche. Das IC-Ware-Grün (#19E56A) erscheint nur als
Markenzeichen; interaktive Elemente nutzen ein dunkleres Grün derselben Familie mit
ausreichendem Kontrast (≥ 4,5 : 1 auf Weiß). Keine Verläufe, Schatten oder Animationen.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..app.presentation import Tone


@dataclass(frozen=True, slots=True)
class Tokens:
    """Farben, Abstände und Maße (Qt-Pixel sind DPI-unabhängig)."""

    window: str = "#F3F3F3"
    surface: str = "#FFFFFF"
    surface_alt: str = "#FAFAFA"
    surface_hover: str = "#F0F0F0"
    border: str = "#DADADA"
    border_strong: str = "#BDBDBD"
    text: str = "#1B1B1B"
    text_secondary: str = "#5C5C5C"
    text_disabled: str = "#9E9E9E"
    accent: str = "#0B6B3A"
    accent_hover: str = "#095C32"
    accent_pressed: str = "#074A28"
    accent_text: str = "#FFFFFF"
    selection: str = "#DDEEE3"
    selection_inactive: str = "#E8E8E8"
    brand: str = "#19E56A"
    brand_ground: str = "#111111"
    radius: int = 4
    control_height: int = 28
    row_height: int = 28
    spacing: int = 8


TONES: dict[Tone, tuple[str, str]] = {
    Tone.INFO: ("#005A9E", "#E6F0FA"),
    Tone.SUCCESS: ("#0B6B3A", "#E4F2E9"),
    Tone.WARNING: ("#8A4B00", "#FFF4D6"),
    Tone.DANGER: ("#B42318", "#FDECEA"),
    Tone.DONE: ("#5C5C5C", "#EDEDED"),
    Tone.NEUTRAL: ("#7A7A7A", "#F0F0F0"),
}

TOKENS = Tokens()
FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Selawik")
BASE_POINT_SIZE = 9.0


def stylesheet(t: Tokens = TOKENS) -> str:
    """Stylesheet aus den Tokens."""
    warn_fg, warn_bg = TONES[Tone.WARNING]
    danger_fg, danger_bg = TONES[Tone.DANGER]
    return f"""
QMainWindow, QDialog {{ background: {t.window}; }}
QWidget {{ color: {t.text}; }}
QToolTip {{
    background: {t.surface};
    color: {t.text};
    border: 1px solid {t.border_strong};
    padding: 4px 6px;
}}

#TopBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
#BrandName {{ font-weight: 600; }}
#ProductName {{ color: {t.text_secondary}; }}
#TopBarInfo {{ color: {t.text_secondary}; }}

#NavBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
QTabBar#CategoryTabs {{ background: transparent; }}
QTabBar#CategoryTabs::tab {{
    background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 7px 12px 6px 12px; margin-right: 2px; color: {t.text_secondary};
}}
QTabBar#CategoryTabs::tab:hover {{ color: {t.text}; background: {t.surface_hover}; }}
QTabBar#CategoryTabs::tab:selected {{
    color: {t.text};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}

QTableView {{
    background: {t.surface};
    alternate-background-color: {t.surface_alt};
    border: 1px solid {t.border};
    gridline-color: transparent;
    selection-background-color: {t.selection};
    selection-color: {t.text};
    outline: 0;
}}
QTableView::item {{ padding: 0 6px; border: none; }}
QTreeView, QListView {{
    background: {t.surface};
    border: 1px solid {t.border};
    selection-background-color: {t.selection};
    selection-color: {t.text};
    outline: 0;
}}
QTreeView::item {{ padding: 4px 2px; }}
QTreeView::item:selected, QListView::item:selected {{ background: {t.selection}; color: {t.text}; }}
QTableView::item:selected:!active {{ background: {t.selection_inactive}; }}
QHeaderView::section {{
    background: {t.surface};
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 5px 6px; font-weight: 600;
}}
QHeaderView::section:hover {{ background: {t.surface_hover}; }}

QTabWidget#Detail::pane {{
    border: 1px solid {t.border};
    border-top: none;
    background: {t.surface};
}}
QTabWidget#Detail > QTabBar::tab {{
    background: {t.window}; border: 1px solid transparent; border-bottom: 1px solid {t.border};
    padding: 6px 10px; color: {t.text_secondary};
}}
QTabWidget#Detail > QTabBar::tab:hover {{ color: {t.text}; }}
QTabWidget#Detail > QTabBar::tab:selected {{
    background: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-bottom-color: {t.surface};
    border-top: 2px solid {t.accent};
}}
QTabWidget#Detail > QTabBar::tab:!selected {{ margin-top: 2px; }}
#DetailHeader {{ background: {t.surface}; border: 1px solid {t.border}; border-bottom: none; }}
#DetailTitle {{ font-size: 11pt; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {t.text_secondary}; }}
#SectionLabel {{ font-weight: 600; color: {t.text}; padding-top: 4px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {t.surface}; }}

QLineEdit, QComboBox, QPlainTextEdit, QDoubleSpinBox, QDateEdit {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: {t.radius}px;
    padding: 3px 6px; min-height: {t.control_height - 10}px; selection-background-color: {t.accent};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border: 1px solid {t.accent};
}}
QLineEdit:read-only, QPlainTextEdit[readOnly="true"] {{
    background: {t.surface_alt};
    color: {t.text};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.text_disabled}; background: {t.window}; }}
QLineEdit[state="review"], QComboBox[state="review"] {{
    border: 1px solid {warn_fg};
    background: {warn_bg};
}}
QLineEdit[state="missing"], QComboBox[state="missing"] {{
    border: 1px solid {danger_fg};
    background: {danger_bg};
}}
QLineEdit#Search {{ min-width: 220px; }}

QPushButton {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: {t.radius}px;
    padding: 4px 14px; min-height: {t.control_height - 10}px;
}}
QPushButton:hover {{ background: {t.surface_hover}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:focus {{ border: 1px solid {t.accent}; }}
QPushButton:disabled {{
    color: {t.text_disabled};
    background: {t.window};
    border-color: {t.border};
}}
QPushButton[role="primary"] {{
    background: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: {t.accent_hover}; }}
QPushButton[role="primary"]:pressed {{ background: {t.accent_pressed}; }}
QPushButton[role="primary"]:disabled {{
    background: {t.border};
    color: {t.text_secondary};
    border-color: {t.border};
}}
QPushButton[role="link"] {{
    background: transparent;
    border: none;
    color: {t.accent};
    padding: 2px 4px;
}}
QPushButton[role="link"]:hover {{ text-decoration: underline; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QStatusBar {{
    background: {t.surface};
    border-top: 1px solid {t.border};
    color: {t.text_secondary};
}}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {t.window}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
#Notice {{ border-radius: {t.radius}px; padding: 8px 10px; }}
#Notice[tone="warning"] {{ background: {warn_bg}; color: {warn_fg}; border: 1px solid #F0D58A; }}
#Notice[tone="danger"] {{ background: {danger_bg}; color: {danger_fg}; border: 1px solid #F3B7B1; }}
#Notice[tone="info"] {{
    background: {TONES[Tone.INFO][1]};
    color: {TONES[Tone.INFO][0]};
    border: 1px solid #B9D3EE;
}}
#Notice[tone="success"] {{
    background: {TONES[Tone.SUCCESS][1]};
    color: {TONES[Tone.SUCCESS][0]};
    border: 1px solid #B7DCC4;
}}
#Metric {{ font-size: 12pt; font-weight: 600; }}
QListWidget#SettingsNav {{
    background: {t.surface};
    border: none;
    border-right: 1px solid {t.border};
    padding-top: 8px;
    outline: 0;
}}
QListWidget#SettingsNav::item {{
    padding: 8px 14px;
    border-left: 3px solid transparent;
    color: {t.text_secondary};
}}
QListWidget#SettingsNav::item:hover {{ background: {t.surface_hover}; color: {t.text}; }}
QListWidget#SettingsNav::item:selected {{
    background: {t.selection};
    color: {t.text};
    border-left: 3px solid {t.accent};
    font-weight: 600;
}}
"""
