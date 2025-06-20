# -*- coding: utf-8 -*-
# nome do arquivo: __init__.py

import sys
import os
import subprocess
import json
import time
import sqlite3
import html
import re
import hashlib
from collections import Counter

from aqt import mw
from aqt.editor import Editor
from aqt.reviewer import Reviewer
from aqt.qt import (
    QWidget, QShortcut, QKeySequence, QLabel, QHBoxLayout,
    QPushButton, QVBoxLayout, QListWidget, QDialog, QFileDialog,
    QListWidgetItem, Qt, QImage, QPixmap, QColor, QLineEdit, QInputDialog,
    QSplitter, QStandardPaths, QUrl, QScrollArea, QIntValidator, QFrame,
    QMovie, QRect, QSize, QRubberBand, QTextEdit, pyqtSignal, QCheckBox,
    QMessageBox, QPoint, QTimer, QTabWidget, QComboBox, QPainter
)
from anki.errors import NotFoundError

try:
    from aqt.theme import theme_manager
except ImportError:
    class FallbackThemeManager:
        def __init__(self):
            try:
                self.night_mode = mw.pm.night_mode()
            except:
                self.night_mode = False
    theme_manager = FallbackThemeManager()

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtCore import QUrl
    PlaybackState = QMediaPlayer.PlaybackState
    IS_PYQT5 = False
except ImportError:
    try:
        from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
        from PyQt5.QtMultimediaWidgets import QVideoWidget
        from PyQt5.QtCore import QUrl
        PlaybackState = QMediaPlayer.State
        IS_PYQT5 = True
    except ImportError:
        QMediaPlayer = None
        QVideoWidget = None

try:
    from aqt.qt import QWebEngineView
except ImportError:
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except ImportError:
        from PyQt5.QtWebEngineWidgets import QWebEngineView

from aqt.gui_hooks import editor_did_init, webview_did_receive_js_message, editor_did_init_buttons
from aqt.utils import showInfo, showWarning, tooltip
from anki.cards import Card

pdf_passwords = {}
last_used_dir = ""

# --- SEÇÃO DE CONFIGURAÇÃO E TRADUÇÃO (MELHORADA) ---

ADDON_PATH = os.path.dirname(__file__)

def load_config():
    """Carrega a configuração do usuário, usando config.json como padrão."""
    try:
        config = mw.addonManager.getConfig(__name__)
        if config:
            return config
    except Exception as e:
        print(f"Could not load user config, falling back to default: {e}")
    
    default_config_path = os.path.join(ADDON_PATH, "config.json")
    if os.path.exists(default_config_path):
        with open(default_config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

config = load_config()

def gc(key, default=None):
    """Obtém um valor da configuração de forma segura."""
    try:
        keys = key.split('.')
        val = config
        for k in keys:
            val = val[k]
        return val
    except (KeyError, TypeError):
        return default

_translations = {}
_language_map = {
    "en_US": "English",
    "pt_BR": "Português (Brasil)"
}

def load_translations():
    """Carrega o arquivo de tradução com base na configuração."""
    global _translations
    lang_code = gc("language", "en_US")
    lang_path = os.path.join(ADDON_PATH, "locales", lang_code, "strings.json")
    
    if not os.path.exists(lang_path):
        lang_path = os.path.join(ADDON_PATH, "locales", "en_US", "strings.json")

    if os.path.exists(lang_path):
        with open(lang_path, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    else:
        _translations = {}

def _(key, **kwargs):
    """Retorna a string traduzida para a chave fornecida."""
    translated_str = _translations.get(key, key)
    if kwargs:
        try:
            return translated_str.format(**kwargs)
        except KeyError:
            return translated_str
    return translated_str

load_translations()

# --- FIM DA SEÇÃO DE CONFIGURAÇÃO E TRADUÇÃO ---


def install_and_load_pymupdf():
    addon_path = os.path.dirname(__file__)
    vendor_path = os.path.join(addon_path, "vendor")
    if vendor_path not in sys.path: sys.path.insert(0, vendor_path)
    try:
        import fitz
        return fitz
    except ImportError: pass
    python_executable = sys.executable
    try:
        mw.progress.start(label="Instalando dependência (PyMuPDF)...", immediate=True)
        cmd = [python_executable, "-m", "pip", "install", "--target", vendor_path, "PyMuPDF"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        mw.progress.finish()
        if result.returncode == 0:
            showInfo("Dependência 'PyMuPDF' instalada com sucesso!\n\nPor favor, reinicie o Anki para ativar a funcionalidade de PDF.")
        else:
            showWarning(f"Ocorreu um erro ao tentar instalar o PyMuPDF.\n\nErro:\n{result.stderr or result.stdout}")
    except Exception as e:
        mw.progress.finish()
        showWarning(f"Ocorreu um erro crítico durante a instalação.\n\nErro: {e}")
    return None
fitz = install_and_load_pymupdf()

def get_main_db_connection():
    db_path = os.path.join(mw.pm.profileFolder(), "item_manager.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS item_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, path TEXT UNIQUE)''')
    cursor.execute("PRAGMA table_info(item_queue)")
    item_queue_columns = [info[1] for info in cursor.fetchall()]
    if 'last_page' not in item_queue_columns:
        cursor.execute("ALTER TABLE item_queue ADD COLUMN last_page INTEGER DEFAULT 1")
    if 'progress' not in item_queue_columns:
        cursor.execute("ALTER TABLE item_queue ADD COLUMN progress TEXT")
    conn.commit()
    return conn

def get_pdf_db_path(pdf_path: str) -> str:
    pdf_hash = hashlib.md5(pdf_path.encode('utf-8')).hexdigest()
    db_name = f"pdf_{pdf_hash}.db"
    return os.path.join(mw.pm.profileFolder(), db_name)

def get_pdf_specific_db_connection(pdf_path: str):
    db_path = get_pdf_db_path(pdf_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pdf_highlights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pdf_path TEXT NOT NULL,
        page_number INTEGER NOT NULL,
        x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
        highlighted_text TEXT,
        UNIQUE (pdf_path, page_number, x0, y0, x1, y1)
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pdf_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pdf_path TEXT NOT NULL,
        page_number INTEGER NOT NULL,
        x0 REAL NOT NULL, y0 REAL NOT NULL,
        comment_text TEXT NOT NULL
    )''')
    conn.commit()
    return conn

def migrate_pdf_data(pdf_path: str):
    new_db_path = get_pdf_db_path(pdf_path)
    if os.path.exists(new_db_path):
        return

    old_db_path = os.path.join(mw.pm.profileFolder(), "item_manager.db")
    if not os.path.exists(old_db_path):
        return

    try:
        old_conn = sqlite3.connect(old_db_path)
        old_cursor = old_conn.cursor()

        old_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_highlights'")
        if old_cursor.fetchone() is None:
            old_conn.close()
            return

        old_cursor.execute("SELECT * FROM pdf_highlights WHERE pdf_path = ?", (pdf_path,))
        highlights = old_cursor.fetchall()
        
        old_cursor.execute("SELECT * FROM pdf_comments WHERE pdf_path = ?", (pdf_path,))
        comments = old_cursor.fetchall()

        if not highlights and not comments:
            old_conn.close()
            return

        new_conn = get_pdf_specific_db_connection(pdf_path)
        new_cursor = new_conn.cursor()

        if highlights:
            new_cursor.executemany("INSERT OR IGNORE INTO pdf_highlights VALUES (?, ?, ?, ?, ?, ?, ?, ?)", highlights)
        if comments:
            new_cursor.executemany("INSERT OR IGNORE INTO pdf_comments VALUES (?, ?, ?, ?, ?, ?)", comments)
        
        new_conn.commit()
        new_conn.close()

        old_cursor.execute("DELETE FROM pdf_highlights WHERE pdf_path = ?", (pdf_path,))
        old_cursor.execute("DELETE FROM pdf_comments WHERE pdf_path = ?", (pdf_path,))
        old_conn.commit()
        old_conn.close()
        
        tooltip("Dados do PDF migrados para o novo formato.")

    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            pass
        else:
            showWarning(f"Erro ao tentar migrar dados do PDF: {e}")
    except Exception as e:
        showWarning(f"Erro inesperado durante a migração de dados do PDF: {e}")


def add_to_item_queue(title, path):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO item_queue (title, path) VALUES (?, ?)", (title, path))
    conn.commit()
    conn.close()

def remove_from_item_queue(path):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM item_queue WHERE path = ?", (path,))
    conn.commit()
    pdf_db_path = get_pdf_db_path(path)
    if os.path.exists(pdf_db_path):
        os.remove(pdf_db_path)
    conn.close()

def get_item_queue():
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title, path FROM item_queue ORDER BY id")
    items = cursor.fetchall()
    conn.close()
    return items

def add_highlight_to_db(pdf_path, page_num, rect, text):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO pdf_highlights (pdf_path, page_number, x0, y0, x1, y1, highlighted_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (pdf_path, page_num, rect.x0, rect.y0, rect.x1, rect.y1, text))
    conn.commit()
    conn.close()

def remove_highlight_from_db(pdf_path, page_num, rect):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pdf_highlights WHERE pdf_path = ? AND page_number = ? AND x0 = ? AND y0 = ? AND x1 = ? AND y1 = ?",
                   (pdf_path, page_num, rect.x0, rect.y0, rect.x1, rect.y1))
    conn.commit()
    conn.close()

def get_highlights_for_page(pdf_path, page_num):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("SELECT x0, y0, x1, y1 FROM pdf_highlights WHERE pdf_path = ? AND page_number = ?", (pdf_path, page_num))
    highlights = [fitz.Rect(x0, y0, x1, y1) for x0, y0, x1, y1 in cursor.fetchall()]
    conn.close()
    return highlights

def add_comment_to_db(pdf_path, page_num, point, text):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pdf_comments (pdf_path, page_number, x0, y0, comment_text) VALUES (?, ?, ?, ?, ?)",
                   (pdf_path, page_num, point.x, point.y, text))
    conn.commit()
    conn.close()

def update_comment_in_db(pdf_path, comment_id, new_text):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE pdf_comments SET comment_text = ? WHERE id = ?", (new_text, comment_id))
    conn.commit()
    conn.close()

def delete_comment_from_db(pdf_path, comment_id):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pdf_comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()

def get_comments_for_page(pdf_path, page_num):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, x0, y0, comment_text FROM pdf_comments WHERE pdf_path = ? AND page_number = ?", (pdf_path, page_num))
    comments = [{"id": id, "point": fitz.Point(x0, y0), "text": text} for id, x0, y0, text in cursor.fetchall()]
    conn.close()
    return comments

def get_item_details(path):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_page, progress FROM item_queue WHERE path = ?", (path,))
    result = cursor.fetchone()
    conn.close()
    if result:
        last_page = result[0] if result[0] is not None else 1
        progress = result[1] if result[1] is not None else '[]'
        return last_page, progress
    return 1, '[]'

def save_last_page(path, page_num):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE item_queue SET last_page = ? WHERE path = ?", (page_num, path))
    conn.commit()
    conn.close()

def save_progress(path, progress_json):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE item_queue SET progress = ? WHERE path = ?", (progress_json, path))
    conn.commit()
    conn.close()

def get_all_highlights_for_pdf(pdf_path):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("SELECT page_number, highlighted_text FROM pdf_highlights WHERE pdf_path = ? ORDER BY page_number, id", (pdf_path,))
    highlights = [{"type": "highlight", "page": row[0], "text": row[1]} for row in cursor.fetchall()]
    conn.close()
    return highlights

def get_all_comments_for_pdf(pdf_path):
    conn = get_pdf_specific_db_connection(pdf_path)
    cursor = conn.cursor()
    cursor.execute("SELECT page_number, comment_text FROM pdf_comments WHERE pdf_path = ? ORDER BY page_number, id", (pdf_path,))
    comments = [{"type": "comment", "page": row[0], "text": row[1]} for row in cursor.fetchall()]
    conn.close()
    return comments

class SelectionLabel(QLabel):
    areaSelected = pyqtSignal(QRect)
    clicked = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rubber_band = None
        self.origin = None
        self.is_selection_mode = False
        self.current_cursor = Qt.CursorShape.PointingHandCursor

    def set_mode(self, mode: str):
        if mode in ["select", "highlight", "select_image"]:
            self.is_selection_mode = True
            self.current_cursor = Qt.CursorShape.CrossCursor
        elif mode == "comment":
            self.is_selection_mode = False
            self.current_cursor = Qt.CursorShape.WhatsThisCursor
        else:
            self.is_selection_mode = False
            self.current_cursor = Qt.CursorShape.PointingHandCursor
        
        self.setCursor(self.current_cursor)
        if self.rubber_band and self.rubber_band.isVisible(): self.rubber_band.hide()


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.is_selection_mode:
                self.origin = event.pos()
                if not self.rubber_band: self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
                self.rubber_band.setGeometry(QRect(self.origin, QSize()))
                self.rubber_band.show()
            else:
                self.clicked.emit(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None: self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.origin is not None and event.button() == Qt.MouseButton.LeftButton:
            selection_rect = self.rubber_band.geometry()
            self.rubber_band.hide()
            self.areaSelected.emit(selection_rect)
            self.origin = None
        super().mouseReleaseEvent(event)

class PdfPageWidget(QWidget):
    areaSelectedOnPage = pyqtSignal(int, QRect)
    pageClicked = pyqtSignal(int, QPoint)

    def __init__(self, page_number: int, parent=None):
        super().__init__(parent)
        self.page_number = page_number
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 10); layout.setSpacing(5)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(0); self.page_label.setWordWrap(True)
        layout.addWidget(self.page_label)
        self.image_label = SelectionLabel()
        self.image_label.setScaledContents(True)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.areaSelected.connect(self.on_area_selected)
        self.image_label.clicked.connect(self.on_label_clicked)
        layout.addWidget(self.image_label)

    def set_pixmap(self, pixmap: QPixmap): self.image_label.setPixmap(pixmap)
    def on_area_selected(self, rect: QRect): self.areaSelectedOnPage.emit(self.page_number, rect)
    def on_label_clicked(self, pos: QPoint): self.pageClicked.emit(self.page_number, pos)
    def set_mode(self, mode: str): self.image_label.set_mode(mode)

class CommentDialog(QDialog):
    def __init__(self, comment_data, pdf_path, parent=None):
        super().__init__(parent)
        self.comment_data = comment_data
        self.pdf_path = pdf_path
        self.setWindowTitle(_("Edit Comment"))
        self.setMinimumWidth(350)
        self.action_status = None

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit(self.comment_data["text"])
        layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()

        delete_button = QPushButton(_("Delete Comment"))
        
        if theme_manager.night_mode:
            bg = gc("colors.delete_button_bg_dark", "#5c1a1a")
            fg = gc("colors.delete_button_fg_dark", "#ff9e9e")
            delete_button.setStyleSheet(f"background-color: {bg}; color: {fg};")
        else:
            bg = gc("colors.delete_button_bg_light", "#ffdddd")
            fg = gc("colors.delete_button_fg_light", "#d8000c")
            delete_button.setStyleSheet(f"background-color: {bg}; color: {fg};")
        
        delete_button.clicked.connect(self.delete_comment)

        save_button = QPushButton(_("Save Changes"))
        save_button.setDefault(True)
        save_button.clicked.connect(self.save_comment)

        close_button = QPushButton(_("Close"))
        close_button.clicked.connect(self.reject)

        button_layout.addWidget(delete_button)
        button_layout.addStretch()
        button_layout.addWidget(save_button)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def save_comment(self):
        new_text = self.text_edit.toPlainText()
        if new_text != self.comment_data["text"]:
            update_comment_in_db(self.pdf_path, self.comment_data["id"], new_text)
            self.action_status = "saved"
        tooltip(_("Comment saved!"))
        self.accept()

    def delete_comment(self):
        reply = QMessageBox.question(self, _('Confirm Deletion'),
                                     _('Are you sure you want to delete this comment?\\nThis action cannot be undone.'),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            delete_comment_from_db(self.pdf_path, self.comment_data["id"])
            self.action_status = "deleted"
            self.accept()

class PdfViewerDialog(QDialog):
    def __init__(self, pdf_path, editor: Editor, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.editor = editor
        self.note = self.editor.note
        self.last_extracted_content = None
        self.quick_add_buttons = []
        self.doc = None
        self.page_widgets = []
        self.current_dpi = gc("zoom.initial_dpi", 75)
        self.total_pages = 0
        self.current_mode = "interact"

        self.cache_dir = os.path.join(ADDON_PATH, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        migrate_pdf_data(self.pdf_path)

        self.search_results = []
        self.current_search_index = -1
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.execute_search)
        
        self.word_counts = None

        last_page, progress_json = get_item_details(self.pdf_path)
        self.last_known_page = last_page
        self.read_pages = set(json.loads(progress_json or '[]'))
        self.current_page_num = self.last_known_page

        self.setMinimumSize(400, 700); self.resize(1100, 800)
        
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.Window)

        if not self._open_pdf_document():
            QTimer.singleShot(0, self.close)
            return

        # Atalhos
        QShortcut(QKeySequence(gc("shortcuts.viewer_focus_search")), self, self.focus_search_bar)
        QShortcut(QKeySequence(gc("shortcuts.viewer_zoom_out")), self, self.zoom_out)
        QShortcut(QKeySequence(gc("shortcuts.viewer_zoom_in")), self, self.zoom_in)
        QShortcut(QKeySequence(gc("shortcuts.viewer_extract_text")), self, lambda: self.set_mode("select"))
        QShortcut(QKeySequence(gc("shortcuts.viewer_extract_image")), self, lambda: self.set_mode("select_image"))
        QShortcut(QKeySequence(gc("shortcuts.viewer_highlight")), self, lambda: self.set_mode("highlight"))
        QShortcut(QKeySequence(gc("shortcuts.viewer_comment")), self, lambda: self.set_mode("comment"))

        self.page_jump_timer = QTimer(self)
        self.page_jump_timer.setSingleShot(True)
        self.page_jump_timer.setInterval(500)
        self.page_jump_timer.timeout.connect(self.jump_to_page_from_input)

        top_layout = QVBoxLayout(self)
        toolbar_layout = self.create_toolbar()
        top_layout.addLayout(toolbar_layout)
        
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_layout.addWidget(main_splitter)

        left_panel = QWidget()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0,0,0,0)
        left_panel.setMinimumWidth(100)

        zoom_layout = QHBoxLayout()
        self.zoom_label = QLabel()
        zoom_layout.addWidget(self.zoom_label)

        self.zoom_out_button = QPushButton(gc("shortcuts.viewer_zoom_out"))
        self.zoom_out_button.clicked.connect(self.zoom_out)
        zoom_layout.addWidget(self.zoom_out_button)

        self.zoom_in_button = QPushButton(gc("shortcuts.viewer_zoom_in"))
        self.zoom_in_button.clicked.connect(self.zoom_in)
        zoom_layout.addWidget(self.zoom_in_button)

        zoom_layout.addStretch()
        left_panel_layout.addLayout(zoom_layout)

        self.tab_widget = QTabWidget()
        self.pdf_list_tab = QWidget()
        self.extraction_tab = self.create_extraction_panel()
        self.annotations_tab = self.create_annotations_panel()
        self.read_pages_tab = self.create_read_pages_panel()
        
        self.pdf_list_tab_index = self.tab_widget.addTab(self.pdf_list_tab, "")
        self.extraction_tab_index = self.tab_widget.addTab(self.extraction_tab, "")
        self.annotations_tab_index = self.tab_widget.addTab(self.annotations_tab, "")
        self.read_pages_tab_index = self.tab_widget.addTab(self.read_pages_tab, "")
        
        if not gc("features.enable_word_index", True):
            self.tab_widget.setTabVisible(self.read_pages_tab_index, False)
        
        left_panel_layout.addWidget(self.tab_widget)
        main_splitter.addWidget(left_panel)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.update_current_page_on_scroll)
        
        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages_layout.setSpacing(10)
        self.scroll_area.setWidget(self.pages_container)
        
        main_splitter.addWidget(self.scroll_area)
        main_splitter.setSizes([250, 850])

        self.setup_pages()
        self.update_ui_texts()

    def _open_pdf_document(self) -> bool:
        """Abre o documento PDF, usando a função auxiliar com prompt."""
        doc = _open_pdf_with_prompt(self, self.pdf_path, prompt_if_needed=True)
        if doc:
            self.doc = doc
            return True
        return False

    def resizeEvent(self, event):
        """Aciona a re-renderização quando a janela é redimensionada."""
        super().resizeEvent(event)

    def create_toolbar(self):
        toolbar_layout = QHBoxLayout()

        self.search_label = QLabel()
        toolbar_layout.addWidget(self.search_label)

        self.search_input = QLineEdit()
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.returnPressed.connect(self.go_to_next_result)
        toolbar_layout.addWidget(self.search_input)

        self.search_prev_button = QPushButton("↑")
        self.search_prev_button.setFixedWidth(30)
        self.search_prev_button.setEnabled(False)
        self.search_prev_button.clicked.connect(self.go_to_prev_result)
        QShortcut(QKeySequence(gc("shortcuts.viewer_prev_search")), self, self.go_to_prev_result)
        toolbar_layout.addWidget(self.search_prev_button)

        self.search_next_button = QPushButton("↓")
        self.search_next_button.setFixedWidth(30)
        self.search_next_button.setEnabled(False)
        self.search_next_button.clicked.connect(self.go_to_next_result)
        QShortcut(QKeySequence(gc("shortcuts.viewer_next_search")), self, self.go_to_next_result)
        toolbar_layout.addWidget(self.search_next_button)

        self.search_results_label = QLabel("")
        toolbar_layout.addWidget(self.search_results_label)

        toolbar_layout.addStretch()

        self.mark_read_button = QPushButton()
        self.mark_read_button.clicked.connect(self.toggle_current_page_read_status)
        toolbar_layout.addWidget(self.mark_read_button)
        self.progress_label = QLabel("")
        toolbar_layout.addWidget(self.progress_label)

        toolbar_layout.addStretch()
        self.prev_page_button = QPushButton()
        self.prev_page_button.clicked.connect(self.go_to_previous_page)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.go_to_previous_page)
        toolbar_layout.addWidget(self.prev_page_button)
        self.page_input = QLineEdit("1"); self.page_input.setFixedWidth(50)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.page_input.returnPressed.connect(self.handle_page_jump_enter)
        self.page_input.textChanged.connect(self.handle_page_input_change)
        toolbar_layout.addWidget(self.page_input)
        self.total_pages_label = QLabel("/ ?")
        toolbar_layout.addWidget(self.total_pages_label)
        self.next_page_button = QPushButton()
        self.next_page_button.clicked.connect(self.go_to_next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.go_to_next_page)
        toolbar_layout.addWidget(self.next_page_button)
        toolbar_layout.addStretch()
        return toolbar_layout

    def update_ui_texts(self):
        """Atualiza todos os textos da UI do visualizador para o idioma atual."""
        self.setWindowTitle(_("PDF Viewer - {filename}", filename=os.path.basename(self.pdf_path)))
        
        # Toolbar
        self.search_label.setText(f"{_('Search')}:")
        self.search_input.setPlaceholderText(_("Search in PDF... (shortcut)", shortcut=gc("shortcuts.viewer_focus_search")))
        self.search_prev_button.setToolTip(_("Previous Result (shortcut)", shortcut=gc("shortcuts.viewer_prev_search")))
        self.search_next_button.setToolTip(_("Next Result (shortcut)", shortcut=gc("shortcuts.viewer_next_search")))
        self.mark_read_button.setToolTip(_("Mark/unmark the current page as read."))
        self.prev_page_button.setText(_("Previous"))
        self.prev_page_button.setToolTip(_("Previous Page (Left Arrow)"))
        self.next_page_button.setText(_("Next"))
        self.next_page_button.setToolTip(_("Next Page (Right Arrow)"))
        
        # Painel Esquerdo
        self.zoom_label.setText(f"<b>{_('Zoom:')}</b>")
        self.zoom_out_button.setToolTip(_("Decrease Zoom"))
        self.zoom_in_button.setToolTip(_("Increase Zoom"))
        
        # Abas
        self.update_pdf_list_tab() # Esta função já usa _()
        self.tab_widget.setTabText(self.extraction_tab_index, _("Extraction"))
        self.tab_widget.setTabText(self.annotations_tab_index, _("Annotations"))
        self.tab_widget.setTabText(self.read_pages_tab_index, _("Read Pages"))
        
        # Aba de Extração
        self.select_area_button.setToolTip(_("Extract Text from Area") + f" ({gc('shortcuts.viewer_extract_text')})")
        self.select_image_button.setToolTip(_("Extract Image from Area") + f" ({gc('shortcuts.viewer_extract_image')})")
        self.highlight_area_button.setToolTip(_("Highlight Area") + f" ({gc('shortcuts.viewer_highlight')})")
        self.comment_button.setToolTip(_("Add a Comment") + f" ({gc('shortcuts.viewer_comment')})")
        self.help_button.setToolTip(_("View extraction shortcuts"))
        self.extracted_text_preview.setPlaceholderText(_("Select text or an image area in the PDF..."))
        self.quick_add_label.setText(f"<b>{_('Targeted Extraction (Quick Add):')}</b>")
        for btn in self.quick_add_buttons:
            field_name = btn.property("fieldName")
            btn.setToolTip(_("Send selection to field '{field_name}'", field_name=field_name))
        
        # Aba de Anotações e Páginas Lidas
        self.populate_annotations_list()
        self.populate_read_pages_list()
        
        # Aba de Índice de Palavras
        if gc("features.enable_word_index", True):
            self.read_pages_label.setText(f"<b>{_('Pages marked as read:')}</b>")
            self.word_index_label.setText(f"<b>{_('Word Index')}</b>")
            self.sort_freq_button.setText(_("Frequency"))
            self.sort_freq_button.setToolTip(_("Sort from most common to least common word"))
            self.sort_alpha_button.setText(_("Alphabetical"))
            self.sort_alpha_button.setToolTip(_("Sort in alphabetical order"))
            self.word_index_list.setToolTip(_("Click on a word to search for it in the PDF."))
            if not self.word_counts:
                self.word_index_total_label.setText(_("Analyzing words..."))
            else:
                self.populate_word_index_list()

        # Atualiza estado geral da UI que depende de texto
        self.update_ui_state()
        for pw in self.page_widgets:
            pw.page_label.setText(f"<b>{_('Page')} {pw.page_number}</b>")

    def focus_search_bar(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    def show_shortcuts_dialog(self):
        title = _("Extraction Shortcuts")
        message = _(
            "<b>Available shortcuts in the 'Extraction' tab:</b><br><br>• <b>Extract Text:</b> <font color='#007bff'>{text_shortcut}</font><br>• <b>Extract Image Area:</b> <font color='#007bff'>{image_shortcut}</font><br>• <b>Highlight Area:</b> <font color='#007bff'>{highlight_shortcut}</font><br>• <b>Add Comment:</b> <font color='#007bff'>{comment_shortcut}</font>",
            text_shortcut=gc("shortcuts.viewer_extract_text"),
            image_shortcut=gc("shortcuts.viewer_extract_image"),
            highlight_shortcut=gc("shortcuts.viewer_highlight"),
            comment_shortcut=gc("shortcuts.viewer_comment")
        )
        QMessageBox.information(self, title, message)

    def on_search_text_changed(self, text):
        self.search_timer.start()

    def clear_search_highlights(self):
        pages_to_refresh = set(page_num for page_num, rect in self.search_results)
        self.search_results.clear()
        self.current_search_index = -1
        for page_num in pages_to_refresh:
            self.load_page(page_num)
        self.update_search_ui()

    def execute_search(self):
        search_text = self.search_input.text()
        
        if self.search_results:
            self.clear_search_highlights()

        if not search_text or len(search_text) < 2:
            self.update_search_ui()
            return

        if not self.doc:
            return

        mw.progress.start(label=_("Searching for '{text}'...", text=search_text), immediate=True)
        
        all_results = []
        for page_num in range(self.total_pages):
            page = self.doc.load_page(page_num)
            found_rects = page.search_for(search_text, quads=False)
            for rect in found_rects:
                all_results.append((page_num + 1, rect))
        
        self.search_results = all_results
        self.current_search_index = -1
        
        mw.progress.finish()
        
        pages_with_results = sorted(list(set(page_num for page_num, rect in self.search_results)))
        for page_num in pages_with_results:
            self.load_page(page_num)

        self.update_search_ui()

        if self.search_results:
            self.go_to_next_result()

    def go_to_next_result(self):
        if not self.search_results:
            return
        self.current_search_index = (self.current_search_index + 1) % len(self.search_results)
        page_num, rect = self.search_results[self.current_search_index]
        self.go_to_page(page_num)
        self.update_search_ui()

    def go_to_prev_result(self):
        if not self.search_results:
            return
        self.current_search_index = (self.current_search_index - 1)
        if self.current_search_index < 0:
            self.current_search_index = len(self.search_results) - 1
        page_num, rect = self.search_results[self.current_search_index]
        self.go_to_page(page_num)
        self.update_search_ui()

    def update_search_ui(self):
        has_results = bool(self.search_results)
        self.search_next_button.setEnabled(has_results)
        self.search_prev_button.setEnabled(has_results)

        if has_results:
            self.search_results_label.setText(_(" {current} of {total} ", current=self.current_search_index + 1, total=len(self.search_results)))
        else:
            if self.search_input.text():
                self.search_results_label.setText(_(" 0 of 0 "))
            else:
                self.search_results_label.setText("")

    def update_pdf_list_tab(self):
        if hasattr(self.pdf_list_tab, 'layout') and self.pdf_list_tab.layout() is not None:
            while self.pdf_list_tab.layout().count():
                child = self.pdf_list_tab.layout().takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        else:
            layout = QVBoxLayout(self.pdf_list_tab)
            layout.setContentsMargins(5, 5, 5, 5)

        pdf_list_widget = QListWidget()
        pdf_list_widget.setWordWrap(True)
        
        all_pdfs = [item for item in get_item_queue() if item[1].lower().endswith(".pdf")]
        
        for title, path in all_pdfs:
            page_count_str = ""
            try:
                doc = fitz.open(path)
                if doc.is_encrypted:
                    cached_pwd = pdf_passwords.get(path)
                    if cached_pwd is not None and doc.authenticate(cached_pwd) > 0:
                        page_count_str = _(" ({count} pgs)", count=doc.page_count)
                    else:
                        page_count_str = f" ({_('Password required')})"
                else:
                    page_count_str = _(" ({count} pgs)", count=doc.page_count)
                doc.close()
            except Exception:
                page_count_str = f" ({_('error')})"

            item_text = f"{title}{page_count_str}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, path)
            
            if path == self.pdf_path:
                item.setText(f"{item_text} ({_('Current')})")
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            
            pdf_list_widget.addItem(item)

        pdf_list_widget.itemClicked.connect(self.on_pdf_list_item_clicked)
        self.pdf_list_tab.layout().addWidget(pdf_list_widget)
        
        self.tab_widget.setTabText(self.pdf_list_tab_index, _("PDFs ({count})", count=len(all_pdfs)))

    def create_extraction_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        top_part_layout = QVBoxLayout()
        tools_layout = QHBoxLayout()
        tools_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.select_area_button = QPushButton("📝")
        self.select_area_button.setCheckable(True); self.select_area_button.setFixedSize(32, 32)
        self.select_area_button.clicked.connect(lambda: self.set_mode("select"))
        tools_layout.addWidget(self.select_area_button)

        self.select_image_button = QPushButton("🖼️")
        self.select_image_button.setCheckable(True); self.select_image_button.setFixedSize(32, 32)
        self.select_image_button.clicked.connect(lambda: self.set_mode("select_image"))
        tools_layout.addWidget(self.select_image_button)

        self.highlight_area_button = QPushButton("🖍️")
        self.highlight_area_button.setCheckable(True); self.highlight_area_button.setFixedSize(32, 32)
        self.highlight_area_button.clicked.connect(lambda: self.set_mode("highlight"))
        tools_layout.addWidget(self.highlight_area_button)

        self.comment_button = QPushButton("💬")
        self.comment_button.setCheckable(True); self.comment_button.setFixedSize(32, 32)
        self.comment_button.clicked.connect(lambda: self.set_mode("comment"))
        tools_layout.addWidget(self.comment_button)

        tools_layout.addSpacing(20)

        self.help_button = QPushButton("?")
        self.help_button.setFixedSize(32, 32)
        self.help_button.clicked.connect(self.show_shortcuts_dialog)
        tools_layout.addWidget(self.help_button)

        tools_layout.addStretch()
        top_part_layout.addLayout(tools_layout)

        self.extracted_text_preview = QTextEdit()
        self.extracted_text_preview.setReadOnly(True)
        self.extracted_text_preview.setMaximumHeight(150)
        top_part_layout.addWidget(self.extracted_text_preview)
        
        layout.addLayout(top_part_layout)

        quick_add_frame = QFrame()
        quick_add_frame.setFrameShape(QFrame.Shape.StyledPanel)
        quick_add_layout = QVBoxLayout(quick_add_frame)
        quick_add_layout.setContentsMargins(5, 5, 5, 5)

        self.quick_add_label = QLabel()
        quick_add_layout.addWidget(self.quick_add_label)

        buttons_layout = QVBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)

        if self.note:
            try:
                flds = self.note.note_type()['flds']
                for fld in flds:
                    field_name = fld['name']
                    field_ord = fld['ord']
                    
                    button = QPushButton(f"-> {field_name}")
                    button.setProperty("fieldOrdinal", field_ord)
                    button.setProperty("fieldName", field_name)
                    button.setEnabled(False)
                    button.clicked.connect(self.send_to_specific_field)
                    
                    buttons_layout.addWidget(button)
                    self.quick_add_buttons.append(button)
            except Exception as e:
                buttons_layout.addWidget(QLabel(_("Error loading fields: {e}", e=e)))
        else:
            buttons_layout.addWidget(QLabel(_("No active note in the editor.")))
        
        buttons_layout.addStretch()
        quick_add_layout.addLayout(buttons_layout)
        
        layout.addWidget(quick_add_frame)
        layout.addStretch()

        return panel

    def create_annotations_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        self.annotations_list = QListWidget()
        self.annotations_list.itemClicked.connect(self.on_annotation_item_clicked)
        self.annotations_list.setWordWrap(True)
        layout.addWidget(self.annotations_list)
        return panel

    def create_read_pages_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.read_pages_label = QLabel()
        layout.addWidget(self.read_pages_label)

        self.read_pages_list = QListWidget()
        self.read_pages_list.itemClicked.connect(self.on_read_page_item_clicked)
        layout.addWidget(self.read_pages_list)

        if gc("features.enable_word_index", True):
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setFrameShadow(QFrame.Shadow.Sunken)
            layout.addWidget(separator)

            self.word_index_label = QLabel()
            layout.addWidget(self.word_index_label)

            sort_buttons_layout = QHBoxLayout()
            self.sort_freq_button = QPushButton()
            self.sort_freq_button.setCheckable(True)
            self.sort_freq_button.clicked.connect(self.sort_words_by_frequency)
            sort_buttons_layout.addWidget(self.sort_freq_button)

            self.sort_alpha_button = QPushButton()
            self.sort_alpha_button.setCheckable(True)
            self.sort_alpha_button.clicked.connect(self.sort_words_alphabetically)
            sort_buttons_layout.addWidget(self.sort_alpha_button)
            layout.addLayout(sort_buttons_layout)

            self.word_index_total_label = QLabel()
            layout.addWidget(self.word_index_total_label)

            self.word_index_list = QListWidget()
            self.word_index_list.itemClicked.connect(self.on_word_index_item_clicked)
            layout.addWidget(self.word_index_list)

        return panel

    def on_word_index_item_clicked(self, item: QListWidgetItem):
        word = item.text().split(" ")[0]
        self.search_input.setText(word)
        self.execute_search()

    def sort_words_alphabetically(self):
        if self.word_counts:
            self.word_counts.sort(key=lambda item: item[0])
            self.populate_word_index_list()
            self.sort_alpha_button.setChecked(True)
            self.sort_freq_button.setChecked(False)

    def sort_words_by_frequency(self):
        if self.word_counts:
            self.word_counts.sort(key=lambda item: item[1], reverse=True)
            self.populate_word_index_list()
            self.sort_freq_button.setChecked(True)
            self.sort_alpha_button.setChecked(False)

    def analyze_pdf_words(self):
        if not self.doc or not gc("features.enable_word_index", True):
            return

        mw.progress.start(label=_("Analyzing PDF words..."), max=self.total_pages, immediate=True)
        
        stop_words = set(gc("word_index.stop_words", []))

        all_words = []
        for page_num in range(self.total_pages):
            page = self.doc.load_page(page_num)
            text = page.get_text("text").lower()
            words_on_page = re.findall(r'\b[a-zA-ZÀ-ú-]{3,}\b', text)
            all_words.extend([word for word in words_on_page if word not in stop_words])
            mw.progress.update(value=page_num + 1)

        mw.progress.finish()

        if not all_words:
            if hasattr(self, "word_index_total_label"):
                self.word_index_total_label.setText(_("No valid words found."))
            return

        counts = Counter(all_words)
        self.word_counts = sorted(counts.items())
        
        self.sort_words_by_frequency()

    def populate_word_index_list(self):
        if not self.word_counts or not gc("features.enable_word_index", True):
            return

        self.word_index_list.clear()
        self.word_index_total_label.setText(f"<b>{_('Total unique words: {count}', count=len(self.word_counts))}</b>")

        for word, count in self.word_counts:
            item_text = _("{word} ({count} times)", word=word, count=count)
            self.word_index_list.addItem(QListWidgetItem(item_text))

    def populate_annotations_list(self):
        if not hasattr(self, "annotations_list"): return
        self.annotations_list.clear()
        all_highlights = get_all_highlights_for_pdf(self.pdf_path)
        all_comments = get_all_comments_for_pdf(self.pdf_path)
        all_annotations = sorted(all_highlights + all_comments, key=lambda x: x['page'])
        if not all_annotations:
            self.annotations_list.addItem(_("No annotations found in this PDF."))
            return
        for annot in all_annotations:
            page_num = annot['page']
            text = (annot['text'] or "").strip()
            display_text = (text[:75] + '...') if len(text) > 75 else text
            if annot['type'] == 'highlight':
                icon = "🖍️"
                item_text = f"{icon} {_('Page')} {page_num}: {display_text}"
            else:
                icon = "💬"
                item_text = f"{icon} {_('Page')} {page_num}: {display_text}"
            list_item = QListWidgetItem(item_text)
            list_item.setData(Qt.ItemDataRole.UserRole, page_num)
            list_item.setToolTip(text)
            self.annotations_list.addItem(list_item)

    def populate_read_pages_list(self):
        if not hasattr(self, "read_pages_list"): return
        self.read_pages_list.clear()
        
        if not self.read_pages:
            self.read_pages_list.addItem(_("No pages marked as read."))
            return

        sorted_pages = sorted(list(self.read_pages))

        for page_num in sorted_pages:
            item_text = f"{_('Page')} {page_num}"
            list_item = QListWidgetItem(item_text)
            list_item.setData(Qt.ItemDataRole.UserRole, page_num)
            self.read_pages_list.addItem(list_item)

    def on_annotation_item_clicked(self, item: QListWidgetItem):
        page_num = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page_num, int):
            self.go_to_page(page_num)

    def on_read_page_item_clicked(self, item: QListWidgetItem):
        page_num = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page_num, int):
            self.go_to_page(page_num)

    def on_pdf_list_item_clicked(self, item: QListWidgetItem):
        new_pdf_path = item.data(Qt.ItemDataRole.UserRole)
        if new_pdf_path and new_pdf_path != self.pdf_path:
            new_viewer = PdfViewerDialog(new_pdf_path, self.editor, self.editor.widget)
            self.editor.pdf_viewer_instance = new_viewer
            new_viewer.show()
            self.close()

    def load_page(self, page_num):
        if not self.doc or not (1 <= page_num <= self.total_pages): return
        page_widget = self.page_widgets[page_num - 1]

        pdf_hash = hashlib.md5(self.pdf_path.encode()).hexdigest()
        cache_filename = f"{pdf_hash}_p{page_num}_d{self.current_dpi}.png"
        cache_filepath = os.path.join(self.cache_dir, cache_filename)

        qpixmap = None
        if os.path.exists(cache_filepath):
            qpixmap = QPixmap(cache_filepath)
        
        if not qpixmap or qpixmap.isNull():
            page = self.doc.load_page(page_num - 1)
            pix = page.get_pixmap(dpi=self.current_dpi)
            qimage = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).rgbSwapped()
            qpixmap = QPixmap.fromImage(qimage)
            qpixmap.save(cache_filepath, "PNG")

        highlights = get_highlights_for_page(self.pdf_path, page_num)
        comments = get_comments_for_page(self.pdf_path, page_num)
        search_rects = [rect for p_num, rect in self.search_results if p_num == page_num]

        if highlights or comments or search_rects:
            temp_pixmap = qpixmap.copy()
            painter = QPainter(temp_pixmap)
            scale = self.current_dpi / 72.0
            
            painter.setBrush(QColor(255, 255, 0, 100))
            painter.setPen(Qt.PenStyle.NoPen)
            for rect in highlights:
                qt_rect = QRect(int(rect.x0 * scale), int(rect.y0 * scale), int(rect.width * scale), int(rect.height * scale))
                painter.drawRect(qt_rect)

            painter.setBrush(QColor(255, 165, 0, 120))
            for rect in search_rects:
                qt_rect = QRect(int(rect.x0 * scale), int(rect.y0 * scale), int(rect.width * scale), int(rect.height * scale))
                painter.drawRect(qt_rect)

            for comment in comments:
                point = comment["point"] * scale
                painter.drawText(QPoint(int(point.x), int(point.y + 10)), "💬")

            painter.end()
            page_widget.set_pixmap(temp_pixmap)
        else:
            page_widget.set_pixmap(qpixmap)

    def setup_pages(self):
        if not self.doc: return

        for widget in self.page_widgets:
            widget.deleteLater()
        self.page_widgets.clear()

        self.total_pages = self.doc.page_count
        if self.total_pages > 0:
            self.page_input.setValidator(QIntValidator(1, self.total_pages, self))
        else:
            self.pages_layout.addWidget(QLabel(_("This PDF contains no pages.")))
            self.update_ui_state()
            return

        for i in range(self.total_pages):
            page_widget = PdfPageWidget(i + 1)
            page_widget.areaSelectedOnPage.connect(self.on_area_selected)
            page_widget.pageClicked.connect(self.on_page_clicked)
            self.pages_layout.addWidget(page_widget)
            self.page_widgets.append(page_widget)

        self.load_all_pages()
        self.populate_annotations_list()
        self.populate_read_pages_list()
        
        if gc("features.enable_word_index", True) and not self.word_counts:
            self.analyze_pdf_words()

        QTimer.singleShot(100, lambda: self.go_to_page(self.last_known_page))

    def load_all_pages(self):
        if not self.doc: return
        mw.progress.start(label=_("Loading {count} pages (Zoom: {dpi} DPI)...", count=self.total_pages, dpi=self.current_dpi), max=self.total_pages, immediate=True)
        for i in range(self.total_pages):
            self.load_page(i + 1)
            mw.progress.update(value=i + 1)
        
        for i in range(self.total_pages):
            self.refresh_page_visuals(i + 1)

        mw.progress.finish()
        self.update_ui_state()

    def update_ui_state(self):
        is_doc_loaded = self.total_pages > 0
        self.page_input.setText(str(self.current_page_num))
        self.total_pages_label.setText(f"/ {self.total_pages}")
        self.prev_page_button.setEnabled(is_doc_loaded and self.current_page_num > 1)
        self.next_page_button.setEnabled(is_doc_loaded and self.current_page_num < self.total_pages)
        self.zoom_in_button.setEnabled(is_doc_loaded)
        self.zoom_out_button.setEnabled(is_doc_loaded and self.current_dpi > 50)
        self.page_input.setEnabled(is_doc_loaded)

        if is_doc_loaded:
            self.progress_label.setText(f"  <b>{_('Progress:')}</b> {len(self.read_pages)} / {self.total_pages} {_('read')}")
            if self.current_page_num in self.read_pages:
                self.mark_read_button.setText(_("Unmark as Read ✓"))
                if theme_manager.night_mode:
                    bg = gc("colors.read_page_bg_dark", "#2c572c")
                    fg = gc("colors.read_page_fg_dark", "#d4edda")
                    self.mark_read_button.setStyleSheet(f"background-color: {bg}; color: {fg};")
                else:
                    bg = gc("colors.read_page_bg_light", "#d4edda")
                    fg = gc("colors.read_page_fg_light", "#155724")
                    self.mark_read_button.setStyleSheet(f"background-color: {bg}; color: {fg};")
            else:
                self.mark_read_button.setText(_("Mark as Read"))
                self.mark_read_button.setStyleSheet("")
            self.mark_read_button.setEnabled(True)
        else:
            self.progress_label.setText("")
            self.mark_read_button.setEnabled(False)

    def update_current_page_on_scroll(self):
        if not self.page_widgets: return
        scroll_bar = self.scroll_area.verticalScrollBar()
        viewport_center = scroll_bar.value() + self.scroll_area.viewport().height() / 2
        closest_page = 1; min_distance = float('inf')
        for i, widget in enumerate(self.page_widgets):
            widget_center = widget.y() + widget.height() / 2
            distance = abs(viewport_center - widget_center)
            if distance < min_distance:
                min_distance = distance; closest_page = i + 1
        if self.current_page_num != closest_page:
            self.current_page_num = closest_page; self.update_ui_state()

    def go_to_page(self, page_num):
        if not (1 <= page_num <= self.total_pages): return
        target_widget = self.page_widgets[page_num - 1]
        self.scroll_area.ensureWidgetVisible(target_widget)
        self.current_page_num = page_num; self.update_ui_state()

    def go_to_previous_page(self): self.go_to_page(self.current_page_num - 1)
    def go_to_next_page(self): self.go_to_page(self.current_page_num + 1)

    def handle_page_input_change(self):
        self.page_jump_timer.start()

    def handle_page_jump_enter(self):
        self.page_jump_timer.stop()
        self.jump_to_page_from_input()

    def jump_to_page_from_input(self):
        try:
            page_to_jump = int(self.page_input.text())
            if 1 <= page_to_jump <= self.total_pages: self.go_to_page(page_to_jump)
            else:
                tooltip(_("Please enter a number between 1 and {total}.", total=self.total_pages))
                self.page_input.setText(str(self.current_page_num))
        except ValueError:
            tooltip(_("Invalid input.")); self.page_input.setText(str(self.current_page_num))

    def zoom_in(self):
        self.current_dpi += gc("zoom.step_dpi", 25)
        tooltip(_("Applying zoom: {dpi} DPI... This may take a moment.", dpi=self.current_dpi))
        self.load_all_pages()

    def zoom_out(self):
        if self.current_dpi > 50:
            self.current_dpi -= gc("zoom.step_dpi", 25)
            tooltip(_("Applying zoom: {dpi} DPI... This may take a moment.", dpi=self.current_dpi))
            self.load_all_pages()

    def refresh_page_visuals(self, page_num):
        if not (1 <= page_num <= len(self.page_widgets)):
            return
        page_widget = self.page_widgets[page_num - 1]
        if page_widget.page_label:
            if page_num in self.read_pages:
                if theme_manager.night_mode:
                    bg = gc("colors.read_page_bg_dark", "#2c572c")
                    fg = gc("colors.read_page_fg_dark", "#d4edda")
                    border = gc("colors.read_page_border_dark", "#4a784a")
                    page_widget.page_label.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 3px; padding: 2px;")
                else:
                    bg = gc("colors.read_page_bg_light", "#e6ffed")
                    fg = gc("colors.read_page_fg_light", "#2ca02c")
                    border = gc("colors.read_page_border_light", "#a3d6a3")
                    page_widget.page_label.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 3px; padding: 2px;")
                page_widget.page_label.setToolTip(_("This page has been marked as read."))
            else:
                page_widget.page_label.setStyleSheet("")
                page_widget.page_label.setToolTip("")

    def toggle_current_page_read_status(self):
        page = self.current_page_num
        if page in self.read_pages:
            self.read_pages.remove(page)
            tooltip(_("Page {page} unmarked.", page=page))
        else:
            self.read_pages.add(page)
            tooltip(_("Page {page} marked as read.", page=page))
        
        progress_json = json.dumps(list(self.read_pages))
        save_progress(self.pdf_path, progress_json)
        
        self.refresh_page_visuals(page)
        self.update_ui_state()
        self.populate_read_pages_list()

    def set_mode(self, mode: str):
        buttons = {
            "select": self.select_area_button,
            "select_image": self.select_image_button,
            "highlight": self.highlight_area_button,
            "comment": self.comment_button
        }

        if self.current_mode == mode:
            self.current_mode = "interact"
            buttons[mode].setChecked(False)
        else:
            self.current_mode = mode
            for btn_mode, button in buttons.items():
                button.setChecked(btn_mode == mode)

        for page_widget in self.page_widgets:
            page_widget.set_mode(self.current_mode)

    def on_page_clicked(self, page_number, click_pos):
        if self.current_mode == "comment":
            text, ok = QInputDialog.getMultiLineText(self, _("Add Comment"), _("Enter your comment:"))
            if ok and text:
                page_widget = self.page_widgets[page_number - 1]
                pdf_point = self._convert_widget_pos_to_pdf_point(page_widget, click_pos)
                if pdf_point:
                    add_comment_to_db(self.pdf_path, page_number, pdf_point, text)
                    self.load_page(page_number)
                    self.populate_annotations_list()
            self.set_mode("interact")
            return

        if self.current_mode == "interact":
            page_widget = self.page_widgets[page_number - 1]
            pdf_point = self._convert_widget_pos_to_pdf_point(page_widget, click_pos)
            if not pdf_point: return

            comments = get_comments_for_page(self.pdf_path, page_number)
            for comment in comments:
                icon_rect = fitz.Rect(comment["point"], comment["point"] + (12, 12))
                if pdf_point in icon_rect:
                    self.handle_comment_click(comment, page_number)
                    return

            highlights = get_highlights_for_page(self.pdf_path, page_number)
            for rect in highlights:
                if pdf_point in rect:
                    self.handle_highlight_click(rect, page_number)
                    return

    def handle_comment_click(self, comment_data, page_number):
        dialog = CommentDialog(comment_data, self.pdf_path, self)
        dialog.exec()

        if dialog.action_status == "saved":
            self.load_page(page_number)
            self.populate_annotations_list()
        elif dialog.action_status == "deleted":
            if self.doc:
                self.doc.close()
            if not self._open_pdf_document():
                self.close()
                return
            self.load_page(page_number)
            self.populate_annotations_list()
            tooltip(_("Comment deleted."))

    def handle_highlight_click(self, rect, page_number):
        reply = QMessageBox.question(self, _('Remove Highlight'), _('Do you want to remove this highlight?'),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            remove_highlight_from_db(self.pdf_path, page_number, rect)
            self.load_page(page_number)
            self.populate_annotations_list()

    def _convert_widget_pos_to_pdf_point(self, page_widget, pos):
        rect = self._convert_widget_pos_to_pdf_rect(page_widget, QRect(pos, QSize(1,1)))
        return fitz.Point(rect.x0, rect.y0) if rect else None

    def _convert_widget_pos_to_pdf_rect(self, page_widget, selection_rect):
        image_label = page_widget.image_label
        if not image_label.pixmap() or image_label.pixmap().isNull(): return None
        pixmap_original_size = image_label.pixmap().size()
        label_size = image_label.size()
        if pixmap_original_size.isEmpty() or label_size.isEmpty(): return None
        pix_w, pix_h = pixmap_original_size.width(), pixmap_original_size.height()
        lbl_w, lbl_h = label_size.width(), label_size.height()
        ratio_pix = pix_w / pix_h if pix_h > 0 else 1
        ratio_lbl = lbl_w / lbl_h if lbl_h > 0 else 1
        if ratio_lbl > ratio_pix:
            scaled_h = lbl_h; scaled_w = int(lbl_h * ratio_pix)
            offset_x = (lbl_w - scaled_w) / 2; offset_y = 0
        else:
            scaled_w = lbl_w; scaled_h = int(lbl_w / ratio_pix)
            offset_x = 0; offset_y = (lbl_h - scaled_h) / 2
        if not QRect(offset_x, offset_y, scaled_w, scaled_h).intersects(selection_rect): return None
        scale_x_ratio = pix_w / scaled_w if scaled_w > 0 else 1
        scale_y_ratio = pix_h / scaled_h if scaled_h > 0 else 1
        pixmap_x0 = (selection_rect.left() - offset_x) * scale_x_ratio
        pixmap_y0 = (selection_rect.top() - offset_y) * scale_y_ratio
        pixmap_x1 = (selection_rect.right() - offset_x) * scale_x_ratio
        pixmap_y1 = (selection_rect.bottom() - offset_y) * scale_y_ratio
        pdf_points_per_pixel = 72.0 / self.current_dpi
        return fitz.Rect(
            pixmap_x0 * pdf_points_per_pixel, pixmap_y0 * pdf_points_per_pixel,
            pixmap_x1 * pdf_points_per_pixel, pixmap_y1 * pdf_points_per_pixel
        )

    def on_area_selected(self, page_number: int, selection_rect: QRect):
        if not self.doc: return
        page_widget = self.page_widgets[page_number - 1]
        pdf_rect = self._convert_widget_pos_to_pdf_rect(page_widget, selection_rect)
        if not pdf_rect: return

        self.last_extracted_content = None
        for btn in self.quick_add_buttons:
            btn.setEnabled(False)
        self.extracted_text_preview.clear()

        if self.current_mode == "highlight":
            try:
                page = self.doc.load_page(page_number - 1)
                highlight_text = page.get_text("text", clip=pdf_rect, sort=True).strip()
                add_highlight_to_db(self.pdf_path, page_number, pdf_rect, highlight_text)
                self.load_page(page_number)
                self.populate_annotations_list()
                tooltip(_("Area highlighted on page {page_number}!", page_number=page_number))
            except Exception as e:
                showWarning(_("Error highlighting area: {e}", e=e))

        elif self.current_mode == "select":
            try:
                page = self.doc.load_page(page_number - 1)
                extracted_text = page.get_text("text", clip=pdf_rect, sort=True)
                if extracted_text.strip():
                    self.last_extracted_content = extracted_text
                    self.extracted_text_preview.setPlainText(extracted_text)
                    for btn in self.quick_add_buttons:
                        btn.setEnabled(True)
                    tooltip(_("Text extracted from page {page_number}!", page_number=page_number))
                else:
                    tooltip(_("No text found in the selected area."))
            except Exception as e:
                showWarning(_("Error extracting text from area: {e}", e=e))

        elif self.current_mode == "select_image":
            try:
                page = self.doc.load_page(page_number - 1)
                pix = page.get_pixmap(clip=pdf_rect, dpi=max(150, self.current_dpi))
                
                fname = f"pdf_clip_{int(time.time())}_{page_number}.png"
                media_path = os.path.join(mw.col.media.dir(), fname)
                pix.save(media_path)
                
                html_for_anki = f'<img src="{fname}">'
                self.last_extracted_content = html_for_anki
                
                image_url = QUrl.fromLocalFile(media_path).toString()
                preview_html = _("Image captured:") + f'<br><img src="{image_url}" style="max-width: 100%;">'
                self.extracted_text_preview.setHtml(preview_html)
                
                for btn in self.quick_add_buttons:
                    btn.setEnabled(True)
                tooltip(_("Image area captured!"))
            except Exception as e:
                showWarning(_("Error extracting image from area: {e}", e=e))

        self.set_mode("interact")

    def send_to_specific_field(self):
        sender_button = self.sender()
        if not sender_button or not self.last_extracted_content:
            tooltip(_("No content selected to send."))
            return

        if not self.editor or not self.editor.note:
            showWarning(_("The editor or the current note is not available."))
            return

        field_ord = sender_button.property("fieldOrdinal")
        field_name = sender_button.property("fieldName")
        content_to_send = self.last_extracted_content

        if not content_to_send.strip().startswith('<'):
            html_to_insert = html.escape(content_to_send).replace('\n', '<br>')
        else:
            html_to_insert = content_to_send

        try:
            current_html = self.editor.note.fields[field_ord]
            separator = "" if not current_html.strip() else "<br>"
            new_html = current_html + separator + html_to_insert
            self.editor.note.fields[field_ord] = new_html
            
            self.editor.loadNote()
            
            tooltip(_("Sent to field '{field_name}'!", field_name=field_name))
        except IndexError:
            showWarning(_("Error: Field '{field_name}' (index {ord}) was not found in the current note.", field_name=field_name, ord=field_ord))
        except Exception as e:
            showWarning(_("An unexpected error occurred while sending to the field: {e}", e=e))

        self.last_extracted_content = None
        for btn in self.quick_add_buttons:
            btn.setEnabled(False)
        self.extracted_text_preview.clear()
        self.extracted_text_preview.setPlaceholderText(_("Select text or an image area in the PDF..."))

    def closeEvent(self, event):
        save_last_page(self.pdf_path, self.current_page_num)
        if self.doc: self.doc.close()
        if hasattr(self.editor, "pdf_viewer_instance") and self.editor.pdf_viewer_instance is self:
            self.editor.pdf_viewer_instance = None
        super().closeEvent(event)

class ContentPreviewDialog(QDialog):
    def __init__(self, html_content: str, window_title: str, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.resize(800, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        zoom_layout = QHBoxLayout()
        zoom_out_button = QPushButton("➖")
        zoom_out_button.setToolTip(_("Decrease Zoom") + " (Ctrl+-)")
        zoom_out_button.setFixedSize(40, 30)
        zoom_out_button.clicked.connect(self.zoom_out)
        
        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        zoom_in_button = QPushButton("➕")
        zoom_in_button.setToolTip(_("Increase Zoom") + " (Ctrl++)")
        zoom_in_button.setFixedSize(40, 30)
        zoom_in_button.clicked.connect(self.zoom_in)

        zoom_layout.addStretch()
        zoom_layout.addWidget(zoom_out_button)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(zoom_in_button)
        zoom_layout.addStretch()
        layout.addLayout(zoom_layout)

        self.webview = QWebEngineView()
        base_url = QUrl.fromLocalFile(mw.col.media.dir() + os.path.sep)
        self.webview.setHtml(html_content, baseUrl=base_url)
        layout.addWidget(self.webview)

        QShortcut(QKeySequence("Ctrl+-"), self, self.zoom_out)
        QShortcut(QKeySequence("Ctrl+="), self, self.zoom_in)

        self.update_zoom_label()

    def zoom_in(self):
        self.webview.setZoomFactor(self.webview.zoomFactor() * 1.1)
        self.update_zoom_label()

    def zoom_out(self):
        self.webview.setZoomFactor(self.webview.zoomFactor() * 0.9)
        self.update_zoom_label()

    def update_zoom_label(self):
        zoom_percentage = int(self.webview.zoomFactor() * 100)
        self.zoom_label.setText(f"{zoom_percentage}%")

class DropItemListWidget(QListWidget):
    def __init__(self, editor: Editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if all(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files_added = 0
        urls = event.mimeData().urls()
        if not urls:
            return

        for url in urls:
            if url.isLocalFile():
                fpath = url.toLocalFile()
                if os.path.isfile(fpath):
                    title = os.path.basename(fpath)
                    add_to_item_queue(title, fpath)
                    files_added += 1
        
        if files_added > 0:
            update_item_list(self.editor)
            tooltip(_("{count} item(s) added successfully.", count=files_added))
            on_item_list_selection_changed(self.editor)
        
        event.accept()

def on_item_list_selection_changed(editor: Editor):
    base_text = f"<b>{_('Item Manager')}</b>"
    label = getattr(editor, "item_manager_label", None)
    if not label: return
    current_item = editor.item_list.currentItem()
    label_text = base_text
    if current_item:
        item_path = current_item.data(Qt.ItemDataRole.UserRole)
        if item_path and item_path.lower().endswith(".pdf") and fitz:
            if os.path.exists(item_path):
                doc = None
                try:
                    doc = fitz.open(item_path)
                    if doc.is_encrypted:
                        cached_pwd = pdf_passwords.get(item_path)
                        if cached_pwd is not None and doc.authenticate(cached_pwd) > 0:
                            label_text = f"{base_text} ({_('{count} pgs', count=doc.page_count)})"
                        else:
                            label_text = f"{base_text} ({_('Password required')})"
                    else:
                        label_text = f"{base_text} ({_('{count} pgs', count=doc.page_count)})"
                except Exception:
                    label_text = f"{base_text} ({_('Error reading PDF')})"
                finally:
                    if doc: doc.close()
            else:
                label_text = f"{base_text} ({_('File not found')})"
    label.setText(label_text)

def add_side_panel(editor: Editor):
    if hasattr(editor, "custom_side_panel"): return
    panel_widget = QWidget()
    
    panel_layout = QVBoxLayout(panel_widget)
    panel_widget.setMinimumWidth(300)
    panel_layout.setContentsMargins(0, 0, 0, 0)
    panel_splitter = QSplitter(Qt.Orientation.Vertical)
    search_container = QWidget()
    search_layout = QVBoxLayout(search_container)
    editor.search_label = QLabel()
    editor.search_label.setStyleSheet("font-size: 16px; font-weight: bold;")
    search_layout.addWidget(editor.search_label)
    search_bar_layout = QHBoxLayout()
    editor.search_bar = QLineEdit()
    editor.search_bar.textChanged.connect(lambda: search_in_collection(editor, editor.search_bar.text()))
    search_bar_layout.addWidget(editor.search_bar)
    editor.link_button = QPushButton()
    editor.link_button.clicked.connect(lambda: on_link_button_clicked(editor))
    search_bar_layout.addWidget(editor.link_button)
    search_layout.addLayout(search_bar_layout)
    editor.tag_search_checkbox = QCheckBox()
    editor.tag_search_checkbox.stateChanged.connect(lambda: search_in_collection(editor, editor.search_bar.text()))
    search_layout.addWidget(editor.tag_search_checkbox)
    editor.search_results = QListWidget()
    editor.search_results.setMinimumHeight(100)
    editor.search_results.itemDoubleClicked.connect(lambda item: show_note_preview_dialog(item, editor))
    search_layout.addWidget(editor.search_results)
    item_manager_container = QWidget()
    item_manager_layout = QVBoxLayout(item_manager_container)
    
    lang_layout = QHBoxLayout()
    editor.lang_label = QLabel()
    lang_layout.addWidget(editor.lang_label)
    
    editor.lang_combo = QComboBox()
    locales_path = os.path.join(ADDON_PATH, "locales")
    if os.path.isdir(locales_path):
        for lang_code in sorted(os.listdir(locales_path)):
            if os.path.isdir(os.path.join(locales_path, lang_code)):
                display_name = _language_map.get(lang_code, lang_code)
                editor.lang_combo.addItem(display_name, lang_code)
    
    current_lang_code = gc("language", "en_US")
    index = editor.lang_combo.findData(current_lang_code)
    if index != -1:
        editor.lang_combo.setCurrentIndex(index)
        
    editor.lang_combo.currentIndexChanged.connect(lambda: on_language_change(editor))
    lang_layout.addWidget(editor.lang_combo)
    item_manager_layout.addLayout(lang_layout)

    editor.item_manager_label = QLabel()
    editor.item_manager_label.setStyleSheet("font-size: 16px; font-weight: bold;")
    item_manager_layout.addWidget(editor.item_manager_label)

    manage_button_layout = QHBoxLayout()
    editor.item_button = QPushButton("➕")
    editor.item_button.clicked.connect(lambda: add_item_dialog(editor))
    manage_button_layout.addWidget(editor.item_button)
    editor.remove_button = QPushButton("➖")
    editor.remove_button.clicked.connect(lambda: on_remove_button_clicked(editor))
    manage_button_layout.addWidget(editor.remove_button)
    item_manager_layout.addLayout(manage_button_layout)

    editor.item_list = DropItemListWidget(editor)
    editor.item_list.itemSelectionChanged.connect(lambda: on_item_list_selection_changed(editor))
    item_manager_layout.addWidget(editor.item_list)

    extract_button_layout = QHBoxLayout()
    editor.extract_image_button = QPushButton("🖼️")
    extract_button_layout.addWidget(editor.extract_image_button)
    editor.extract_text_button = QPushButton("📄")
    extract_button_layout.addWidget(editor.extract_text_button)
    editor.view_pdf_button = QPushButton("👁️")
    extract_button_layout.addWidget(editor.view_pdf_button)
    item_manager_layout.addLayout(extract_button_layout)

    panel_splitter.addWidget(search_container)
    panel_splitter.addWidget(item_manager_container)
    panel_splitter.setSizes([250, 250])
    panel_layout.addWidget(panel_splitter)
    old_layout = editor.widget.layout()
    original_content_widget = QWidget()
    original_content_widget.setLayout(old_layout)
    main_splitter = QSplitter(Qt.Orientation.Horizontal)
    main_splitter.addWidget(original_content_widget)
    main_splitter.addWidget(panel_widget)
    main_splitter.setSizes([800, 400])
    new_main_layout = QHBoxLayout(editor.widget)
    new_main_layout.setContentsMargins(0, 0, 0, 0)
    new_main_layout.addWidget(main_splitter)
    editor.custom_side_panel = panel_widget
    
    editor.extract_image_button.clicked.connect(lambda: on_extract_button_clicked(editor, as_image=True))
    editor.extract_text_button.clicked.connect(lambda: on_extract_button_clicked(editor, as_image=False))
    editor.view_pdf_button.clicked.connect(lambda: open_pdf_viewer_dialog(editor))

    update_editor_ui_texts(editor)
    update_item_list(editor)
    panel_widget.setVisible(False)

def update_editor_ui_texts(editor: Editor):
    """Atualiza todos os textos do painel lateral do editor para o idioma selecionado."""
    editor.search_label.setText(f"<b>{_('Search in Collection')}</b>")
    editor.search_bar.setPlaceholderText(_("Type to search notes or tags..."))
    editor.link_button.setText(_("Link"))
    editor.link_button.setToolTip(_("Insert a link to the selected card"))
    editor.tag_search_checkbox.setText(_("Search by tags"))
    editor.tag_search_checkbox.setToolTip(_("Search for tags (including subtags). Space = OR. Hyphen = NOT."))
    editor.lang_label.setText(f"<b>{_('Language')}:</b>")
    editor.item_manager_label.setText(f"<b>{_('Item Manager')}</b>")
    editor.item_button.setToolTip(_("Add item"))
    editor.remove_button.setToolTip(_("Remove selected item"))
    editor.extract_image_button.setToolTip(_("Extract Page as Image"))
    editor.extract_text_button.setToolTip(_("Extract Page as HTML Layout"))
    editor.view_pdf_button.setToolTip(_("View PDF"))
    update_item_list(editor) # Para atualizar textos como "Nenhum item..."
    on_item_list_selection_changed(editor)

def on_language_change(editor: Editor):
    """Salva a nova escolha de idioma, recarrega as traduções e atualiza a UI."""
    lang_code = editor.lang_combo.currentData()
    config['language'] = lang_code
    mw.addonManager.writeConfig(__name__, config)
    
    load_translations()
    
    update_editor_ui_texts(editor)
    
    if hasattr(editor, "pdf_viewer_instance") and editor.pdf_viewer_instance:
        editor.pdf_viewer_instance.update_ui_texts()
        
    tooltip(_("Language changed to: {lang}", lang=editor.lang_combo.currentText()))

def add_toggle_button(buttons: list, editor: Editor):
    def on_toggle(ed: Editor):
        if not hasattr(ed, "custom_side_panel"):
            showWarning(_("The side panel was not found. An error may have occurred during initialization."))
            return
        panel = ed.custom_side_panel
        new_visibility = not panel.isVisible()
        panel.setVisible(new_visibility)
    
    btn = editor.addButton(
        icon=None, 
        cmd="toggle_custom_panel_special", 
        func=on_toggle, 
        tip=_("Show/Hide Side Panel") + f" ({gc('shortcuts.editor_toggle_panel')})", 
        keys=gc("shortcuts.editor_toggle_panel"), 
        label="<b>⧉</b>"
    )
    buttons.append(btn)
    return buttons

def show_note_preview_dialog(item_or_nid, parent_context):
    nid = None
    cid = None
    
    if isinstance(item_or_nid, QListWidgetItem):
        data = item_or_nid.data(Qt.ItemDataRole.UserRole)
        if not data or 'nid' not in data: return
        nid = data['nid']
        cid = data.get('cid')
    elif isinstance(item_or_nid, int):
        nid = item_or_nid
    else:
        return

    parent_widget = parent_context.widget if hasattr(parent_context, 'widget') else mw
    
    if not hasattr(parent_context, "preview_dialogs"):
        parent_context.preview_dialogs = []

    try:
        note = mw.col.get_note(nid)
        model = note.note_type()
        
        html_parts = []
        
        card_ids = mw.col.card_ids_of_note(nid)
        if card_ids:
            card = mw.col.get_card(cid or card_ids[0])
            deck_name = mw.col.decks.name(card.did)
            deck_html = f"""
                <div style="margin-bottom: 15px; padding: 8px 12px; border: 1px solid #a0c4ff; background-color: #e7f0ff; border-radius: 4px; font-size: 1.1em;">
                    <strong>{_('Deck:')}</strong> {html.escape(deck_name)}
                </div>
            """
            html_parts.append(deck_html)

        for i, fld in enumerate(model['flds']):
            field_name = fld['name']
            field_content = note.fields[i]
            
            if not field_content.strip():
                continue

            processed_content = re.sub(r'\[sound:(.*?)\]', r'<audio controls src="\1"></audio>', field_content)

            html_parts.append(f"""
                <div style="border: 1px solid #ccc; border-radius: 4px; padding: 10px; margin-bottom: 15px; background-color: #f9f9f9;">
                    <h3 style="margin-top: 0; font-size: 1.1em; color: #555; border-bottom: 1px solid #eee; padding-bottom: 5px;">
                        {html.escape(field_name)}
                    </h3>
                    <div>{processed_content}</div>
                </div>
            """)

        if note.tags:
            tags_html = "".join([
                f'<span style="display: inline-block; background-color: #e0e0e0; color: #333; padding: 2px 8px; margin: 2px; border-radius: 10px; font-size: 0.9em;">{html.escape(tag)}</span>'
                for tag in note.tags
            ])
            html_parts.append(f"""
                <div style="border: 1px solid #ccc; border-radius: 4px; padding: 10px; margin-bottom: 15px; background-color: #f0f8ff;">
                    <h3 style="margin-top: 0; font-size: 1.1em; color: #555; border-bottom: 1px solid #eee; padding-bottom: 5px;">
                        {_('Tags')}
                    </h3>
                    <div>{tags_html}</div>
                </div>
            """)
        
        full_html = f"<body class='{'night-mode' if theme_manager.night_mode else ''}'>{''.join(html_parts)}</body>"

        dialog = ContentPreviewDialog(full_html, _("Linked Card: Note {nid}", nid=nid), parent_widget)
        parent_context.preview_dialogs.append(dialog)
        dialog.show()
        dialog.finished.connect(lambda: parent_context.preview_dialogs.remove(dialog))

    except (ValueError, IndexError, NotFoundError) as e:
        showWarning(_("Could not preview card: {e}", e=e))

def on_js_message(handled: tuple[bool, any], message: str, context: any) -> tuple[bool, any]:
    if handled[0]:
        return handled

    if isinstance(context, Reviewer) and message.startswith("open_linked_card:"):
        try:
            nid = int(message.split(":")[1])
            show_note_preview_dialog(nid, context)
        except (ValueError, IndexError) as e:
            showWarning(_("Error opening linked card: {e}", e=e))
        
        return (True, None)

    return handled

def on_link_button_clicked(editor: Editor):
    current_item = editor.search_results.currentItem()
    if not current_item: tooltip(_("Select a card from the search list.")); return
    data = current_item.data(Qt.ItemDataRole.UserRole)
    if not data or 'nid' not in data:
        showWarning(_("Selected item does not have a valid note ID."))
        return
    nid = data['nid']
    button_html = f'<button data-nid="{nid}" onclick="pycmd(\'open_linked_card:{nid}\'); return false;" style="background-color: #007bff; color: white; border: none; padding: 5px 10px; border-radius: 4px; cursor: pointer;">{_("View Linked Card")} (ID: {nid})</button>'
    html_to_insert = f'<div>{button_html}</div><div></div>'
    editor.web.eval(f"document.execCommand('insertHTML', false, {json.dumps(html_to_insert)});")
    tooltip(_("Link to note {nid} inserted.", nid=nid))

def clean_field_for_display(field_content: str) -> str:
    if not field_content: return ""
    text = field_content
    text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    def media_replacer(match): filename = os.path.basename(match.group(1)); return f" [{_('Media')}: {filename}] "
    text = re.sub(r'<(?:img|audio|video)[^>]*?src="([^"]*)"[^>]*?>', media_replacer, text, flags=re.IGNORECASE)
    def complex_video_replacer(match):
        video_block = match.group(0); src_match = re.search(r'src="([^"]*)"', video_block, flags=re.IGNORECASE)
        if src_match: filename = os.path.basename(src_match.group(1)); return f" [{_('Media')}: {filename}] "
        return f" [{_('Video')}] "
    text = re.sub(r'<video.*?</video>', complex_video_replacer, text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[sound:(.*?)\]', r' [{_("Audio")}: \1] ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text); text = html.unescape(text); text = ' '.join(text.split())
    return text.strip()

def search_in_collection(editor: Editor, text: str):
    editor.search_results.clear()
    is_tag_search = editor.tag_search_checkbox.isChecked()
    if not text.strip():
        if hasattr(editor, "search_label"): editor.search_label.setText(f"<b>{_('Search in Collection')}</b>")
        return
    note_ids = []
    query = ""
    if is_tag_search:
        include_terms = []
        exclude_terms = []
        for term in text.split():
            if not term: continue
            if term.startswith('-'):
                clean_term = term[1:]
                if clean_term: exclude_terms.append(clean_term)
            else:
                include_terms.append(term)
        query_parts = []
        if include_terms:
            or_parts = [f"tag:*{term}*" for term in include_terms]
            if len(or_parts) > 1: query_parts.append(f"({' OR '.join(or_parts)})")
            else: query_parts.append(or_parts[0])
        if exclude_terms:
            exclude_parts = [f"-tag:*{term}*" for term in exclude_terms]
            query_parts.extend(exclude_parts)
        query = " ".join(query_parts)
    else:
        query = text
    if query:
        try:
            note_ids = mw.col.find_notes(query)
        except Exception as e:
            if hasattr(editor, "search_label"): editor.search_label.setText(f"<b>{_('Error in search: {e}', e=e)}</b>")
            return
    total_found = len(note_ids)
    display_limit = 100
    results_to_display = note_ids[:display_limit]
    if hasattr(editor, "search_label"): editor.search_label.setText(f"<b>{_('Search in Collection ({found}/{total})', found=len(results_to_display), total=total_found)}</b>")
    for nid in results_to_display:
        note = mw.col.get_note(nid)
        if not note: continue

        card_ids = mw.col.card_ids_of_note(nid)
        if not card_ids: continue
        first_cid = card_ids[0]

        all_fields_content = []
        for field_text in note.fields:
            cleaned_text = clean_field_for_display(field_text)
            if cleaned_text: all_fields_content.append(cleaned_text)
        if not all_fields_content: continue
        tags_str = _("Tags: {tags}", tags=', '.join(note.tags)) if note.tags else _("No tags")
        full_content_str = " | ".join(all_fields_content)
        list_item = QListWidgetItem(_("Note {nid}: {content}\n[{tags}]", nid=nid, content=full_content_str, tags=tags_str))

        item_data = {'nid': nid, 'cid': first_cid}
        list_item.setData(Qt.ItemDataRole.UserRole, item_data)
        editor.search_results.addItem(list_item)








def add_item_dialog(editor: Editor):
    """
    Abre o diálogo de arquivo e FORÇA a janela do editor a voltar ao foco.
    """
    # global last_used_dir # Descomente se estiver usando a variável global

    # Guarda a referência da janela do editor ANTES de qualquer coisa.
    editor_window = editor.widget

    # Abre a porra do diálogo de arquivo.
    fpath, __ = QFileDialog.getOpenFileName(
        mw, 
        _("Select a file"), 
        # last_used_dir # Descomente se estiver usando a variável global
    )

    # Processa o arquivo SE o usuário selecionou um.
    if fpath:
        # last_used_dir = os.path.dirname(fpath) # Descomente se estiver usando a variável global
        
        title = os.path.basename(fpath)
        add_to_item_queue(title, fpath)
        update_item_list(editor)
        tooltip(_("'{title}' added.", title=title))
        on_item_list_selection_changed(editor)

    # --- A CORREÇÃO FINAL E BRUTA ---
    # DEPOIS DE TUDO, não importa se o usuário cancelou ou não,
    # mandamos a janela do editor de volta pra frente.
    editor_window.raise_()
    editor_window.activateWindow()
    editor_window.setFocus()

def on_remove_button_clicked(editor: Editor):
    current_item = editor.item_list.currentItem()
    if not current_item: showInfo(_("Select an item to remove.")); return
    item_path = current_item.data(Qt.ItemDataRole.UserRole)
    if item_path:
        remove_from_item_queue(item_path)
        update_item_list(editor); tooltip(_("Item removed."))
        on_item_list_selection_changed(editor)
    else:
        tooltip(_("This item cannot be removed."))

def update_item_list(editor: Editor):
    if not hasattr(editor, "item_list"): return
    editor.item_list.clear()
    items = get_item_queue()
    if not items: editor.item_list.addItem(_("No items in the list."))
    else:
        for title, path in items:
            list_item = QListWidgetItem(title)
            list_item.setData(Qt.ItemDataRole.UserRole, path)
            editor.item_list.addItem(list_item)
    if not fitz:
        status_item = QListWidgetItem(_("PyMuPDF not installed. PDF extraction disabled."))
        status_item.setForeground(QColor("orange")); editor.item_list.addItem(status_item)
    on_item_list_selection_changed(editor)

def check_pdf_selection(editor: Editor):
    if not fitz: showWarning(_("PyMuPDF is required.")); return None
    current_item = editor.item_list.currentItem()
    if not current_item: showInfo(_("Select a PDF from the list.")); return None
    pdf_path = current_item.data(Qt.ItemDataRole.UserRole)
    if not pdf_path or not pdf_path.lower().endswith(".pdf"): showInfo(_("The item is not a PDF.")); return None
    if not os.path.exists(pdf_path): showWarning(_("File not found:\n{path}", path=pdf_path)); return None
    return pdf_path

def open_pdf_viewer_dialog(editor: Editor):
    if hasattr(editor, "pdf_viewer_instance") and editor.pdf_viewer_instance:
        editor.pdf_viewer_instance.activateWindow()
        tooltip(_("The PDF viewer is already open."))
        return
    pdf_path = check_pdf_selection(editor)
    if pdf_path:
        editor.pdf_viewer_instance = PdfViewerDialog(pdf_path, editor, editor.widget)
        if not editor.pdf_viewer_instance.doc:
            editor.pdf_viewer_instance = None
        else:
            editor.pdf_viewer_instance.show()

def _open_pdf_with_prompt(parent_widget, pdf_path: str, prompt_if_needed: bool = True):
    """
    Função auxiliar para abrir um PDF, pedindo senha se necessário e usando um cache.
    Retorna o objeto 'doc' ou None se falhar.
    """
    try:
        doc = fitz.open(pdf_path)
        if doc.is_encrypted:
            cached_password = pdf_passwords.get(pdf_path)
            if cached_password is not None:
                if doc.authenticate(cached_password) > 0:
                    return doc

            if not prompt_if_needed:
                doc.close()
                return None

            password = ""
            while True:
                if doc.authenticate(password) > 0:
                    pdf_passwords[pdf_path] = password
                    return doc

                prompt_text = _("This PDF is password protected. Please enter the password:")
                if password:
                    prompt_text = _("Incorrect password. Please try again:")
                
                password, ok = QInputDialog.getText(
                    parent_widget, 
                    _("Password Required"), 
                    prompt_text, 
                    QLineEdit.EchoMode.Password
                )
                if not ok:
                    doc.close()
                    return None
        else:
            return doc
    except Exception as e:
        showWarning(_("Error processing the PDF:\n{e}", e=e))
        return None

def on_extract_button_clicked(editor: Editor, as_image: bool):
    pdf_path = check_pdf_selection(editor)
    if not pdf_path: return
    
    doc = _open_pdf_with_prompt(editor.widget, pdf_path, prompt_if_needed=True)
    if not doc: return

    extract_type = _("IMAGE") if as_image else _("HTML LAYOUT")
    max_pages = doc.page_count if doc.page_count > 0 else 1
    
    page_num, ok = QInputDialog.getInt(editor.widget, _("Extract Page as {extract_type}", extract_type=extract_type), _("What page number do you want to extract?"), 1, 1, max_pages)
    
    if ok:
        if as_image:
            insert_pdf_page_as_image(editor, doc, page_num, pdf_path)
        else:
            insert_pdf_page_as_html_layout(editor, doc, page_num, pdf_path)
    
    doc.close()

def insert_pdf_page_as_image(editor: Editor, doc, page_number: int, pdf_path: str):
    try:
        if not 1 <= page_number <= doc.page_count: showWarning(_("Invalid page. The PDF has {count} pages.", count=doc.page_count)); return
        page = doc.load_page(page_number - 1); pix = page.get_pixmap(dpi=200)

        fname = f"pdf_page_{int(time.time())}_{os.path.basename(pdf_path)}_{page_number}.png"
        media_path = os.path.join(mw.col.media.dir(), fname); pix.save(media_path)
        
        unique_class = f"pdf-image-inverter-{page_number}-{int(time.time())}"
        style_block = f"""
        <style>
          .{unique_class} {{
            display: inline-block;
          }}
          .night-mode .{unique_class} img {{
            filter: invert(1) hue-rotate(180deg);
          }}
        </style>
        """
        html_to_insert = f'{style_block}<div class="{unique_class}"><img src="{fname}"></div>'

        editor.web.eval(f"document.execCommand('insertHTML', false, {json.dumps(html_to_insert)});")
        tooltip(_("Page {page_number} inserted as IMAGE!", page_number=page_number))
    except Exception as e: showWarning(_("Error processing the PDF:\n{e}", e=e))

def srgb_int_to_css(srgb_int):
    if not isinstance(srgb_int, int): return "var(--text-fg)"
    r = (srgb_int >> 16) & 0xFF
    g = (srgb_int >> 8) & 0xFF
    b = srgb_int & 0xFF
    return f"rgb({r}, {g}, {b})"

def insert_pdf_page_as_html_layout(editor: Editor, doc, page_number: int, pdf_path: str):
    try:
        if not 1 <= page_number <= doc.page_count:
            showWarning(_("Invalid page. The PDF has {count} pages.", count=doc.page_count))
            return

        page = doc.load_page(page_number - 1)
        
        text_page = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES)
        
        temp_page_for_bg = doc.load_page(page_number - 1)
        for block in text_page["blocks"]:
            if block["type"] == 0:
                for line in block["lines"]:
                    for span in line["spans"]:
                        temp_page_for_bg.add_redact_annot(span["bbox"], fill=(1, 1, 1))
        
        temp_page_for_bg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        
        dpi = gc("extraction.background_dpi", 150)
        pix = temp_page_for_bg.get_pixmap(dpi=dpi)
        bg_img_fname = f"pdf_bg_clean_{int(time.time())}_{page_number}.png"
        media_path = os.path.join(mw.col.media.dir(), bg_img_fname)
        pix.save(media_path)
        
        scale = dpi / 72.0
        html_elements = []
        font_size_multiplier = gc("extraction.font_size_multiplier", 0.8)

        for block in text_page["blocks"]:
            if block['type'] == 0:
                for line in block["lines"]:
                    for span in line["spans"]:
                        span_text = span["text"]
                        if not span_text.strip(): continue

                        span_bbox = fitz.Rect(span['bbox'])
                        style = (
                            f"position: absolute; "
                            f"left: {span_bbox.x0 * scale}px; "
                            f"top: {span_bbox.y0 * scale}px; "
                            f"font-family: '{span['font']}', sans-serif; "
                            f"font-size: {span['size'] * scale * font_size_multiplier}px; "
                            f"color: {srgb_int_to_css(span['color'])}; "
                            f"font-weight: {'bold' if 'bold' in span['font'].lower() else 'normal'}; "
                            f"white-space: nowrap; "
                        )
                        html_elements.append(f'<div contenteditable="true" style="{style}">{html.escape(span_text)}</div>')
        
        reconstructed_page = f"""<div style="position: relative; width: {pix.width}px; height: {pix.height}px; background-image: url('{bg_img_fname}'); background-size: 100% 100%; overflow: hidden;">{''.join(html_elements)}</div>"""
        
        unique_class = f"pdf-layout-wrapper-{int(time.time())}"
        style_block = f"""
        <style>
          .night-mode .{unique_class} {{
            background-color: #333 !important;
            border-color: #555 !important;
          }}
          .night-mode .{unique_class} p, .night-mode .{unique_class} i, .night-mode .{unique_class} div {{
             color: var(--text-fg) !important;
          }}
          .night-mode .{unique_class} div[style*="background-image"] {{
             filter: invert(1) hue-rotate(180deg);
          }}
        </style>
        """
        styled_html = (
            f'{style_block}'
            f'<div class="{unique_class}" style="border: 1px solid #ccc; padding: 15px; margin: 10px 0; background-color: #f9f9f9; display: inline-block;">'
            f'<p style="font-size: 0.8em; color: #888; margin-top: 0;">'
            f'<i>{_("Extracted from PDF:")} {os.path.basename(pdf_path)}, {_("Page:")} {page_number}</i></p><hr>'
            f'{reconstructed_page}</div>'
        )
        
        editor.web.eval(f"document.execCommand('insertHTML', false, {json.dumps(styled_html)});")
        tooltip(_("Page {page_number} reconstructed with editable text!", page_number=page_number))

    except Exception as e:
        showWarning(_("Error reconstructing the PDF page:\n{e}", e=e))
    # A cláusula 'finally' foi removida. A função on_extract_button_clicked
    # que chama esta, já é responsável por fechar o documento.

editor_did_init.append(add_side_panel)
editor_did_init_buttons.append(add_toggle_button)
webview_did_receive_js_message.append(on_js_message)