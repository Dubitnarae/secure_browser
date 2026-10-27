import sys
import ctypes
import json
import os
import re
from urllib.parse import quote_plus
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLineEdit, QVBoxLayout, QHBoxLayout,
                             QWidget, QToolBar, QStatusBar, QTextEdit, QDockWidget,
                             QInputDialog, QListWidget, QListWidgetItem, QDialog,
                             QPushButton, QMessageBox)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtCore import QUrl, Qt, QObject, QEvent, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut

VERSION = "1.5"

# 방문 기록 최대 보관 개수 (넘으면 오래된 것부터 삭제)
MAX_HISTORY = 1000
# 보안 모드: 다운로드 허용 여부
ALLOW_DOWNLOADS = False

# 데이터 파일은 실행 위치와 상관없이 프로그램이 있는 폴더에 저장
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 도메인(또는 localhost, IPv4) + 선택적 포트/경로
URL_RE = re.compile(
    r'^(localhost|(\d{1,3}\.){3}\d{1,3}|([\w-]+\.)+[^\W\d_]{2,})(:\d+)?([/?#].*)?$',
    re.IGNORECASE)


def resolve_input(text):
    """주소창 입력을 이동할 URL로 바꾼다. 주소가 아니면 구글 검색 주소를 돌려준다."""
    text = text.strip()
    if re.match(r'^(https?|file)://', text, re.IGNORECASE):
        return text
    if ' ' not in text and URL_RE.match(text):
        # 내 컴퓨터/내부망 주소는 보통 https 인증서가 없으므로 http로 연다
        local = re.match(r'^(localhost|(\d{1,3}\.){3}\d{1,3})(:\d+)?([/?#]|$)', text, re.IGNORECASE)
        return ('http://' if local else 'https://') + text
    return "https://www.google.com/search?q=" + quote_plus(text)


class CaptureKeyFilter(QObject):
    """웹 화면에 포커스가 있어도 PrintScreen 키를 감지하기 위한 전역 필터"""
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Print:
            self.callback()
        return False


class SecurePage(QWebEnginePage):
    """새 창(target=_blank, window.open)을 같은 화면에서 열어주는 페이지"""
    def createWindow(self, window_type):
        temp = QWebEnginePage(self.profile(), self)
        temp.urlChanged.connect(lambda url: (self.setUrl(url), temp.deleteLater()))
        return temp


class LoggingSecureBrowser(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("YD Browser - 보안 모드")
        self.resize(1200, 900)

        self.data_file = os.path.join(BASE_DIR, "browser_data.json")
        self.log_file = os.path.join(BASE_DIR, "security.log")
        self.startup_notice = None
        self.load_data()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.browser = QWebEngineView()
        self.page = SecurePage(self.browser.page().profile(), self.browser)
        self.browser.setPage(self.page)
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
        self.setup_shortcuts()
        self.setup_security_policy()
        self.apply_light_theme()

        self.browser.urlChanged.connect(self.on_url_changed)
        self.browser.titleChanged.connect(self.on_title_changed)
        self.browser.loadProgress.connect(self.on_load_progress)

        self.add_log(f"브라우저 시작 (v{VERSION})")
        if self.startup_notice:
            self.add_log("⚠️ " + self.startup_notice)

    # ---------- 데이터 저장/불러오기 ----------
    def load_data(self):
        self.bookmarks, self.history = [], []
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.bookmarks = data.get("bookmarks", [])
            self.history = data.get("history", [])
        except Exception:
            # 깨진 파일은 덮어쓰지 않고 따로 보관
            backup = self.data_file + ".broken_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                os.replace(self.data_file, backup)
            except OSError:
                pass
            self.startup_notice = f"저장 파일이 손상되어 {os.path.basename(backup)} 로 보관하고 새로 시작합니다."

    def save_data(self):
        self.history = self.history[-MAX_HISTORY:]
        data = {"bookmarks": self.bookmarks, "history": self.history}
        tmp = self.data_file + ".tmp"
        try:
            # 임시 파일에 쓴 뒤 교체 → 쓰는 도중 종료돼도 원본이 깨지지 않음
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(tmp, self.data_file)
        except OSError as e:
            self.add_log(f"⚠️ 저장 실패: {e}")

    # ---------- 화면 구성 ----------
    def setup_toolbar(self):
        navbar = QToolBar()
        navbar.setMovable(False)
        self.addToolBar(navbar)

        back_btn = QAction('◀', self)
        back_btn.setToolTip("뒤로 (Alt+←)")
        back_btn.triggered.connect(self.browser.back)
        navbar.addAction(back_btn)

        forward_btn = QAction('▶', self)
        forward_btn.setToolTip("앞으로 (Alt+→)")
        forward_btn.triggered.connect(self.browser.forward)
        navbar.addAction(forward_btn)

        reload_btn = QAction('🔄', self)
        reload_btn.setToolTip("새로고침 (F5)")
        reload_btn.triggered.connect(self.browser.reload)
        navbar.addAction(reload_btn)

        bookmark_btn = QAction('⭐ 북마크', self)
        bookmark_btn.triggered.connect(self.add_bookmark)
        navbar.addAction(bookmark_btn)

        show_bookmarks_btn = QAction('📂 목록', self)
        show_bookmarks_btn.triggered.connect(self.show_bookmarks)
        navbar.addAction(show_bookmarks_btn)

        history_btn = QAction('🕘 기록', self)
        history_btn.triggered.connect(self.show_history)
        navbar.addAction(history_btn)

        # 주소창 (여기에 검색 로직이 통합됨)
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("주소 입력 또는 구글 검색 (Ctrl+L)")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        navbar.addWidget(self.url_bar)

        log_toggle_btn = QAction('📋 로그', self)
        log_toggle_btn.setCheckable(True)
        log_toggle_btn.triggered.connect(self.toggle_log_window)
        navbar.addAction(log_toggle_btn)
        self.log_toggle_btn = log_toggle_btn

    def setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_url_bar)
        QShortcut(QKeySequence("F6"), self, activated=self.focus_url_bar)
        QShortcut(QKeySequence("F5"), self, activated=self.browser.reload)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.browser.back)
        QShortcut(QKeySequence("Alt+Right"), self, activated=self.browser.forward)

    def focus_url_bar(self):
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    def setup_security_policy(self):
        profile = self.browser.page().profile()
        profile.downloadRequested.connect(self.on_download_requested)
        # 카메라·마이크·위치 등 권한 요청은 모두 거부
        self.page.permissionRequested.connect(self.on_permission_requested)

    def on_download_requested(self, download):
        name = download.downloadFileName()
        if ALLOW_DOWNLOADS:
            download.accept()
            self.add_log(f"다운로드 허용: {name}")
        else:
            download.cancel()
            self.add_log(f"⚠️ 다운로드 차단: {name}")
            self.status_bar.showMessage(f"🚫 다운로드가 차단되었습니다: {name}", 5000)

    def on_permission_requested(self, permission):
        permission.deny()
        self.add_log(f"⚠️ 권한 요청 거부: {permission.permissionType().name} ({permission.origin().host()})")

    # ---------- 페이지 이벤트 ----------
    def on_url_changed(self, q):
        url = q.toString()
        self.url_bar.setText(url)
        if not self.history or self.history[-1]['url'] != url:
            self.history.append({"url": url, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            self.save_data()

    def on_title_changed(self, title):
        self.setWindowTitle(f"{title} - YD Browser (보안 모드)" if title else "YD Browser - 보안 모드")

    def on_load_progress(self, percent):
        if percent < 100:
            self.status_bar.showMessage(f"불러오는 중... {percent}%")
        else:
            self.status_bar.showMessage("보안 시스템 가동 중 | 스마트 검색 활성화")

    # --- 핵심: 스마트 검색/이동 로직 ---
    def navigate_to_url(self):
        input_text = self.url_bar.text().strip()
        if not input_text:
            return
        self.browser.setUrl(QUrl(resolve_input(input_text)))

    # ---------- 북마크 / 기록 ----------
    def add_bookmark(self):
        url = self.browser.url().toString()
        if any(b['url'] == url for b in self.bookmarks):
            self.status_bar.showMessage("이미 북마크에 있는 주소입니다.", 3000)
            return
        title, ok = QInputDialog.getText(self, "북마크 추가", "이름:", QLineEdit.EchoMode.Normal, self.browser.title())
        if ok and title:
            self.bookmarks.append({"title": title, "url": url})
            self.save_data()
            self.status_bar.showMessage("북마크에 추가했습니다.", 3000)

    def show_bookmarks(self):
        self.show_list_dialog("북마크 목록", self.bookmarks)

    def show_history(self):
        self.show_list_dialog("방문 기록", self.history, is_history=True)

    def fill_list(self, list_widget, data_list, is_history):
        # 주소는 항목에 따로 저장 (표시 글자에서 잘라내지 않음)
        list_widget.clear()
        for index in range(len(data_list) - 1, -1, -1):
            item = data_list[index]
            if is_history:
                text = f"{item['time']}  {item['url']}"
            else:
                text = f"{item['title']} ({item['url']})"
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, item['url'])
            row.setData(Qt.ItemDataRole.UserRole + 1, index)
            list_widget.addItem(row)

    def show_list_dialog(self, title, data_list, is_history=False):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(450, 520)
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        self.fill_list(list_widget, data_list, is_history)
        list_widget.itemDoubleClicked.connect(lambda row: self.load_from_list(row, dialog))
        layout.addWidget(list_widget)

        buttons = QHBoxLayout()
        if is_history:
            clear_btn = QPushButton("기록 전체 삭제")
            clear_btn.clicked.connect(lambda: self.clear_history(list_widget))
            buttons.addWidget(clear_btn)
        else:
            del_btn = QPushButton("선택 삭제")
            del_btn.clicked.connect(lambda: self.delete_bookmark(list_widget))
            buttons.addWidget(del_btn)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(dialog.close)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        dialog.exec()

    def load_from_list(self, row, dialog):
        self.browser.setUrl(QUrl(row.data(Qt.ItemDataRole.UserRole)))
        dialog.close()

    def delete_bookmark(self, list_widget):
        row = list_widget.currentItem()
        if row is None:
            return
        del self.bookmarks[row.data(Qt.ItemDataRole.UserRole + 1)]
        self.save_data()
        self.fill_list(list_widget, self.bookmarks, False)  # 번호가 밀리므로 다시 채움

    def clear_history(self, list_widget):
        answer = QMessageBox.question(self, "기록 삭제", "방문 기록을 모두 삭제할까요?")
        if answer == QMessageBox.StandardButton.Yes:
            self.history = []
            self.save_data()
            list_widget.clear()
            self.add_log("방문 기록 삭제")

    # ---------- 로그 / 보안 ----------
    def toggle_log_window(self):
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self.dock.show()
        self.log_toggle_btn.setChecked(self.dock.isVisible())

    def add_log(self, message):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.log_viewer.append(line)
        # 프로그램을 꺼도 남도록 파일에도 기록
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def on_capture_key(self):
        self.add_log("⚠️ 캡처 시도 감지 (PrintScreen) - 화면은 보호 중")
        if not self.dock.isVisible():
            self.toggle_log_window()
        self.status_bar.setStyleSheet("background-color: #e74c3c; color: white;")
        self.status_bar.showMessage("🚨 보안 경고: 캡처 동작 감지!", 7000)
        QTimer.singleShot(7000, lambda: self.status_bar.setStyleSheet(""))
        # 클립보드에 복사된 내용 제거 (캡처 직후 들어오므로 잠시 뒤에 비움)
        QTimer.singleShot(300, QApplication.clipboard().clear)

    def apply_light_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f6fa; }
            QToolBar { background-color: #ffffff; border-bottom: 1px solid #dcdde1; padding: 5px; spacing: 8px; }
            QLineEdit { background-color: #f1f2f6; color: #2f3640; border: 1px solid #dcdde1; padding: 6px; border-radius: 5px; font-size: 14px; }
            QStatusBar { background-color: #ffffff; color: #2f3640; border-top: 1px solid #dcdde1; }
        """)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_affinity_done", False):
            return
        self._affinity_done = True
        try:
            hwnd = int(self.winId())
            ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x01))
        except Exception:
            ok = False
        if ok:
            self.add_log("화면 캡처 방지 적용됨")
        else:
            self.add_log("⚠️ 화면 캡처 방지 적용 실패 - 보호되지 않은 상태입니다")
            QMessageBox.warning(self, "보안 경고", "화면 캡처 방지를 적용하지 못했습니다.\n이 상태에서는 화면이 캡처될 수 있습니다.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LoggingSecureBrowser()
    key_filter = CaptureKeyFilter(window.on_capture_key)
    app.installEventFilter(key_filter)
    window.show()
    sys.exit(app.exec())
