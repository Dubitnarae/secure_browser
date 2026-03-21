import sys
import ctypes
import json
import os
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLineEdit, QVBoxLayout, 
                             QWidget, QToolBar, QStatusBar, QTextEdit, QDockWidget,
                             QInputDialog, QListWidget, QDialog, QPushButton)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QAction

class LoggingSecureBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("YD Browser - 보안 모드")
        self.resize(1200, 900)
        
        self.data_file = "browser_data.json"
        self.load_data()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("https://www.google.com"))
        self.layout.addWidget(self.browser)

        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("background-color: #ffffff; color: #2c3e50; font-family: Consolas; border: none;")
        
        self.dock = QDockWidget("Security Logs", self)
        self.dock.setWidget(self.log_viewer)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide() 

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("보안 시스템 가동 중 | 스마트 검색 활성화")

        self.setup_toolbar()
        self.apply_light_theme()
        
        self.browser.urlChanged.connect(self.on_url_changed)

    def load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.bookmarks = data.get("bookmarks", [])
                    self.history = data.get("history", [])
            except:
                self.bookmarks, self.history = [], []
        else:
            self.bookmarks, self.history = [], []

    def save_data(self):
        data = {"bookmarks": self.bookmarks, "history": self.history}
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def setup_toolbar(self):
        navbar = QToolBar()
        navbar.setMovable(False)
        self.addToolBar(navbar)

        back_btn = QAction('◀', self)
        back_btn.triggered.connect(self.browser.back)
        navbar.addAction(back_btn)

        reload_btn = QAction('🔄', self)
        reload_btn.triggered.connect(self.browser.reload)
        navbar.addAction(reload_btn)

        bookmark_btn = QAction('⭐ 북마크', self)
        bookmark_btn.triggered.connect(self.add_bookmark)
        navbar.addAction(bookmark_btn)

        show_bookmarks_btn = QAction('📂 목록', self)
        show_bookmarks_btn.triggered.connect(self.show_bookmarks)
        navbar.addAction(show_bookmarks_btn)

        # 주소창 (여기에 검색 로직이 통합됨)
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("주소 입력 또는 구글 검색")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        navbar.addWidget(self.url_bar)
        
        log_toggle_btn = QAction('📋 로그', self)
        log_toggle_btn.setCheckable(True)
        log_toggle_btn.triggered.connect(self.toggle_log_window)
        navbar.addAction(log_toggle_btn)

    def on_url_changed(self, q):
        url = q.toString()
        self.url_bar.setText(url)
        if not self.history or self.history[-1]['url'] != url:
            self.history.append({"url": url, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            self.save_data()

    # --- 핵심: 스마트 검색/이동 로직 ---
    def navigate_to_url(self):
        input_text = self.url_bar.text().strip()
        if not input_text: return

        # 1. URL 패턴인지 확인 (공백이 없고 점이 있거나 프로토콜로 시작하는 경우)
        is_url = ('.' in input_text and ' ' not in input_text) or \
                 input_text.startswith(('http://', 'https://', 'file://', 'localhost'))

        if is_url:
            if not input_text.startswith(('http://', 'https://', 'file://')):
                final_url = 'https://' + input_text
            else:
                final_url = input_text
        else:
            # 2. URL이 아니면 검색어로 판단 (기본: 구글 검색)
            # 네이버를 원하시면: f"https://search.naver.com/search.naver?query={input_text}"
            final_url = f"https://www.google.com/search?q={input_text}"

        self.browser.setUrl(QUrl(final_url))

    def add_bookmark(self):
        url = self.browser.url().toString()
        title, ok = QInputDialog.getText(self, "북마크 추가", "이름:", QLineEdit.EchoMode.Normal, self.browser.title())
        if ok and title:
            self.bookmarks.append({"title": title, "url": url})
            self.save_data()

    def show_bookmarks(self):
        self.show_list_dialog("북마크 목록", self.bookmarks)

    def show_history(self):
        self.show_list_dialog("방문 기록", self.history, is_history=True)

    def show_list_dialog(self, title, data_list, is_history=False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(400, 500)
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        for item in reversed(data_list):
            display_text = item['url'] if is_history else f"{item['title']} ({item['url']})"
            list_widget.addItem(display_text)
        list_widget.itemDoubleClicked.connect(lambda item: self.load_from_list(item, dialog, is_history))
        layout.addWidget(list_widget)
        dialog.exec()

    def load_from_list(self, item, dialog, is_history):
        text = item.text()
        url = text if is_history else text.split('(')[-1].strip(')')
        self.browser.setUrl(QUrl(url))
        dialog.close()

    def toggle_log_window(self):
        if self.dock.isVisible(): self.dock.hide()
        else: self.dock.show()

    def add_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_viewer.append(f"[{timestamp}] {message}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Print:
            self.add_log("⚠️ 캡처 차단됨 (PrintScreen)")
            if not self.dock.isVisible(): self.dock.show()
            self.status_bar.setStyleSheet("background-color: #e74c3c; color: white;")
            self.status_bar.showMessage("🚨 보안 경고: 캡처 동작 감지!", 7000)
        super().keyPressEvent(event)

    def apply_light_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f6fa; }
            QToolBar { background-color: #ffffff; border-bottom: 1px solid #dcdde1; padding: 5px; spacing: 8px; }
            QLineEdit { background-color: #f1f2f6; color: #2f3640; border: 1px solid #dcdde1; padding: 6px; border-radius: 5px; font-size: 14px; }
            QStatusBar { background-color: #ffffff; color: #2f3640; border-top: 1px solid #dcdde1; }
        """)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x01)
        except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LoggingSecureBrowser()
    window.show()
    sys.exit(app.exec())