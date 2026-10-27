import sys
import ctypes
import json
import os
import re
import time
from ctypes import wintypes
from urllib.parse import quote_plus
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLineEdit, QVBoxLayout, QHBoxLayout,
                             QWidget, QToolBar, QStatusBar, QTextEdit, QDockWidget,
                             QInputDialog, QListWidget, QListWidgetItem, QDialog,
                             QPushButton, QMessageBox, QTabWidget, QStackedWidget, QLabel,
                             QFormLayout, QComboBox, QCheckBox, QDialogButtonBox)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtCore import QUrl, Qt, QObject, QEvent, QTimer, QByteArray
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QIcon, QDesktopServices

APP_NAME = "YD Browser"
VERSION = "1.6"
GITHUB_REPO = "Dubitnarae/secure_browser"

MAX_HISTORY = 1000          # 방문 기록 최대 보관 개수 (넘으면 오래된 것부터 삭제)
MAX_TABS = 20               # 동시에 열 수 있는 탭 수
LOG_MAX_BYTES = 1_000_000   # 보안 로그가 이 크기를 넘으면 새 파일로 교체
LOG_KEEP = 3                # 보관할 지난 로그 파일 개수
CAPTURE_CHECK_MS = 5000     # 녹화·원격 프로그램 확인 주기

# 데이터 파일은 실행 위치와 상관없이 프로그램이 있는 폴더에 저장
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """exe 안에 포함된 파일(아이콘 등)의 위치"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------------------------------------------------------------- 설정
SEARCH_ENGINES = {
    "google": ("구글", "https://www.google.com/search?q="),
    "naver": ("네이버", "https://search.naver.com/search.naver?query="),
    "bing": ("빙", "https://www.bing.com/search?q="),
    "duckduckgo": ("덕덕고", "https://duckduckgo.com/?q="),
}
CAPTURE_ACTIONS = {"off": "확인 안 함", "warn": "경고만", "block": "화면 가리기"}
START_MODES = {"home": "시작 페이지", "last": "마지막 탭 복원"}

DEFAULT_SETTINGS = {
    "home_url": "https://www.google.com",
    "search_engine": "google",
    "start_mode": "home",
    "allow_downloads": False,
    "save_history": True,
    "capture_tool_action": "warn",
    "check_updates": True,
    "last_update_check": "",
}


def load_settings(path):
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, default in DEFAULT_SETTINGS.items():
            if key in data and type(data[key]) is type(default):
                settings[key] = data[key]
    except (OSError, ValueError, AttributeError):
        pass
    if settings["search_engine"] not in SEARCH_ENGINES:
        settings["search_engine"] = DEFAULT_SETTINGS["search_engine"]
    if settings["capture_tool_action"] not in CAPTURE_ACTIONS:
        settings["capture_tool_action"] = DEFAULT_SETTINGS["capture_tool_action"]
    if settings["start_mode"] not in START_MODES:
        settings["start_mode"] = DEFAULT_SETTINGS["start_mode"]
    return settings


def save_settings(path, settings):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 주소창 입력 해석
# 도메인(또는 localhost, IPv4) + 선택적 포트/경로
URL_RE = re.compile(
    r'^(localhost|(\d{1,3}\.){3}\d{1,3}|([\w-]+\.)+[^\W\d_]{2,})(:\d+)?([/?#].*)?$',
    re.IGNORECASE)
LOCAL_RE = re.compile(r'^(localhost|(\d{1,3}\.){3}\d{1,3})(:\d+)?([/?#]|$)', re.IGNORECASE)


def resolve_input(text, engine="google"):
    """주소창 입력을 이동할 URL로 바꾼다. 주소가 아니면 검색 주소를 돌려준다."""
    text = text.strip()
    if re.match(r'^(https?|file)://', text, re.IGNORECASE):
        return text
    if ' ' not in text and URL_RE.match(text):
        # 내 컴퓨터/내부망 주소는 보통 https 인증서가 없으므로 http로 연다
        return ('http://' if LOCAL_RE.match(text) else 'https://') + text
    return SEARCH_ENGINES.get(engine, SEARCH_ENGINES["google"])[1] + quote_plus(text)


def parse_version(text):
    """'v1.6' → (1, 6)"""
    return tuple(int(n) for n in re.findall(r'\d+', text))


# ---------------------------------------------------------------- 저장 데이터 암호화 (Windows DPAPI)
# 현재 Windows 사용자 계정으로만 풀 수 있다. 다른 PC·다른 계정에서는 열 수 없다.
MAGIC = b"YDB1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _dpapi(protect, data):
    crypt32 = ctypes.WinDLL("crypt32")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.addressof(buf))
    blob_out = _Blob()
    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        ok = fn(ctypes.byref(blob_in), "YD Browser", None, None, None, 1, ctypes.byref(blob_out))
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI 실패")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def dump_store(path, obj):
    """JSON을 암호화해 저장한다. 암호화에 성공하면 True, 못 해서 평문으로 저장했으면 False."""
    raw = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    encrypted = True
    try:
        payload = MAGIC + _dpapi(True, raw)
    except Exception:
        payload, encrypted = raw, False
    tmp = path + ".tmp"
    # 임시 파일에 쓴 뒤 교체 → 쓰는 도중 종료돼도 원본이 깨지지 않음
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, path)
    return encrypted


def load_store(path):
    """암호화된 파일과 예전 평문 JSON(v1.5 이하)을 모두 읽는다."""
    with open(path, "rb") as f:
        payload = f.read()
    if payload.startswith(MAGIC):
        payload = _dpapi(False, payload[len(MAGIC):])
    return json.loads(payload.decode("utf-8-sig"))


# ---------------------------------------------------------------- 녹화·원격 프로그램 감지
CAPTURE_TOOLS = {
    "obs64.exe": "OBS Studio", "obs32.exe": "OBS Studio",
    "bandicam.exe": "Bandicam", "sharex.exe": "ShareX", "greenshot.exe": "Greenshot",
    "lightshot.exe": "Lightshot", "gyazo.exe": "Gyazo", "snagit32.exe": "Snagit",
    "snagiteditor.exe": "Snagit", "camtasia.exe": "Camtasia", "camrecorder.exe": "Camtasia",
    "anydesk.exe": "AnyDesk", "teamviewer.exe": "TeamViewer",
    "teamviewer_service.exe": "TeamViewer", "rustdesk.exe": "RustDesk",
    "parsecd.exe": "Parsec", "vncviewer.exe": "VNC", "tvnserver.exe": "VNC",
    "winvnc.exe": "VNC", "remoting_host.exe": "Chrome 원격 데스크톱",
}
SM_REMOTESESSION = 0x1000


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def running_process_names():
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return set()
    names = set()
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def find_capture_tools():
    """실행 중인 화면 녹화·캡처·원격 제어 프로그램 이름 목록"""
    try:
        found = {CAPTURE_TOOLS[n] for n in running_process_names() if n in CAPTURE_TOOLS}
        if ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION):
            found.add("원격 데스크톱 세션")
    except Exception:
        return []
    return sorted(found)


# ---------------------------------------------------------------- 로그 파일 관리
def rotate_log(path, max_bytes=LOG_MAX_BYTES, keep=LOG_KEEP):
    """로그가 너무 커지면 security.log.1, .2 ... 로 넘기고 오래된 것은 지운다."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return
        oldest = f"{path}.{keep}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(keep - 1, 0, -1):
            src = f"{path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{path}.{i + 1}")
        os.replace(path, path + ".1")
    except OSError:
        pass


# ---------------------------------------------------------------- 화면 구성 요소
class CaptureKeyFilter(QObject):
    """웹 화면에 포커스가 있어도 PrintScreen 키를 감지하기 위한 전역 필터"""
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.last = 0.0

    def eventFilter(self, obj, event):
        # PrintScreen은 키를 누를 때가 아니라 뗄 때만 앱에 전달되는 경우가 있어 둘 다 본다
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) \
                and event.key() == Qt.Key.Key_Print:
            now = time.monotonic()
            if now - self.last > 0.5:
                self.last = now
                self.callback()
        return False


class SecurePage(QWebEnginePage):
    """새 창(target=_blank, window.open)을 새 탭으로 열어주는 페이지"""
    def __init__(self, profile, parent, open_tab):
        super().__init__(profile, parent)
        self.open_tab = open_tab

    def createWindow(self, window_type):
        return self.open_tab()


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window_ref = window
        s = window.settings
        self.setWindowTitle("설정")
        self.setMinimumWidth(460)
        form = QFormLayout(self)

        self.home = QLineEdit(s["home_url"])
        form.addRow("시작 페이지", self.home)

        self.engine = QComboBox()
        for key, (label, _) in SEARCH_ENGINES.items():
            self.engine.addItem(label, key)
        self.engine.setCurrentIndex(self.engine.findData(s["search_engine"]))
        form.addRow("검색 엔진", self.engine)

        self.start_mode = QComboBox()
        for key, label in START_MODES.items():
            self.start_mode.addItem(label, key)
        self.start_mode.setCurrentIndex(self.start_mode.findData(s["start_mode"]))
        form.addRow("시작할 때", self.start_mode)

        self.save_history = QCheckBox("방문 기록·마지막 탭 저장 (끄면 새 기록만 중단)")
        self.save_history.setChecked(s["save_history"])
        form.addRow("기록", self.save_history)

        self.allow_downloads = QCheckBox("다운로드 허용 (끄면 차단)")
        self.allow_downloads.setChecked(s["allow_downloads"])
        form.addRow("다운로드", self.allow_downloads)

        self.capture = QComboBox()
        for key, label in CAPTURE_ACTIONS.items():
            self.capture.addItem(label, key)
        self.capture.setCurrentIndex(self.capture.findData(s["capture_tool_action"]))
        form.addRow("녹화·원격 프로그램 감지 시", self.capture)

        self.check_updates = QCheckBox("하루 한 번 새 버전 확인 (GitHub에 접속)")
        self.check_updates.setChecked(s["check_updates"])
        form.addRow("업데이트", self.check_updates)

        update_btn = QPushButton("지금 새 버전 확인")
        update_btn.clicked.connect(lambda: window.check_for_updates(manual=True))
        form.addRow("", update_btn)

        form.addRow(QLabel(f"{APP_NAME} v{VERSION}"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        home = self.home.text().strip() or DEFAULT_SETTINGS["home_url"]
        return {
            "home_url": resolve_input(home) if not re.match(r'^(https?|file)://', home, re.I) else home,
            "search_engine": self.engine.currentData(),
            "start_mode": self.start_mode.currentData(),
            "save_history": self.save_history.isChecked(),
            "allow_downloads": self.allow_downloads.isChecked(),
            "capture_tool_action": self.capture.currentData(),
            "check_updates": self.check_updates.isChecked(),
        }


class LoggingSecureBrowser(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"{APP_NAME} - 보안 모드")
        self.resize(1200, 900)
        icon_file = resource_path("icon.ico")
        if os.path.exists(icon_file):
            self.setWindowIcon(QIcon(icon_file))

        self.settings_file = os.path.join(BASE_DIR, "settings.json")
        self.data_file = os.path.join(BASE_DIR, "browser_data.json")
        self.log_file = os.path.join(BASE_DIR, "security.log")
        self.settings = load_settings(self.settings_file)
        self.startup_notice = None
        self.plain_warned = False
        self.detected = set()
        self.load_data()

        # --- 화면: 탭 + 찾기 줄 / (녹화 프로그램 감지 시) 가림막
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        main_layout.addWidget(self.tabs)
        self.setup_find_bar(main_layout)
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setWordWrap(True)
        self.cover.setStyleSheet("background-color: #2f3640; color: white; font-size: 18px; padding: 40px;")
        self.stack.addWidget(main_page)
        self.stack.addWidget(self.cover)

        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("background-color: #ffffff; color: #2c3e50; font-family: Consolas; border: none;")
        self.dock = QDockWidget("Security Logs", self)
        self.dock.setWidget(self.log_viewer)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(self.idle_message())

        self.setup_toolbar()
        self.setup_shortcuts()
        self.apply_light_theme()

        # --- 보안 정책 (다운로드 차단은 프로필, 권한 거부는 페이지마다)
        self.profile = QWebEngineProfile.defaultProfile()
        self.profile.downloadRequested.connect(self.on_download_requested)

        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.restore_geometry()
        self.open_start_tabs()

        self.add_log(f"브라우저 시작 (v{VERSION})")
        if self.startup_notice:
            self.add_log("⚠️ " + self.startup_notice)

        # --- 감시 타이머 / 업데이트 확인
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self.check_capture_tools)
        self.capture_timer.start(CAPTURE_CHECK_MS)
        QTimer.singleShot(0, self.check_capture_tools)

        self.net = QNetworkAccessManager(self)
        today = datetime.now().strftime("%Y-%m-%d")
        if self.settings["check_updates"] and self.settings["last_update_check"] != today:
            QTimer.singleShot(3000, lambda: self.check_for_updates(manual=False))

    # ---------- 데이터 저장/불러오기 ----------
    def load_data(self):
        self.bookmarks, self.history, self.session = [], [], {}
        if not os.path.exists(self.data_file):
            return
        try:
            data = load_store(self.data_file)
            self.bookmarks = [b for b in data.get("bookmarks", [])
                              if isinstance(b, dict) and "url" in b and "title" in b]
            self.history = [h for h in data.get("history", [])
                            if isinstance(h, dict) and "url" in h and "time" in h]
            self.session = data.get("session", {}) if isinstance(data.get("session"), dict) else {}
        except Exception:
            # 깨졌거나 풀 수 없는 파일은 덮어쓰지 않고 따로 보관
            backup = self.data_file + ".broken_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                os.replace(self.data_file, backup)
            except OSError:
                pass
            self.startup_notice = (f"저장 파일을 읽지 못해 {os.path.basename(backup)} 로 보관하고 새로 시작합니다. "
                                   "(다른 Windows 계정·PC에서 만든 파일이면 열 수 없습니다)")

    def save_data(self):
        self.history = self.history[-MAX_HISTORY:]
        data = {"bookmarks": self.bookmarks, "history": self.history, "session": self.session}
        try:
            encrypted = dump_store(self.data_file, data)
        except OSError as e:
            self.add_log(f"⚠️ 저장 실패: {e}")
            return
        if not encrypted and not self.plain_warned:
            self.plain_warned = True
            self.add_log("⚠️ 암호화에 실패해 평문으로 저장했습니다")

    def save_app_settings(self):
        try:
            save_settings(self.settings_file, self.settings)
        except OSError as e:
            self.add_log(f"⚠️ 설정 저장 실패: {e}")

    # ---------- 화면 구성 ----------
    def idle_message(self):
        return "보안 시스템 가동 중 | 스마트 검색 활성화"

    def setup_toolbar(self):
        navbar = QToolBar()
        navbar.setMovable(False)
        self.addToolBar(navbar)

        def add_action(text, tip, slot):
            action = QAction(text, self)
            if tip:
                action.setToolTip(tip)
            action.triggered.connect(slot)
            navbar.addAction(action)
            return action

        add_action('◀', "뒤로 (Alt+←)", self.go_back)
        add_action('▶', "앞으로 (Alt+→)", self.go_forward)
        add_action('🔄', "새로고침 (F5)", self.reload_page)
        add_action('➕', "새 탭 (Ctrl+T)", lambda: self.new_tab())
        add_action('⭐ 북마크', "", self.add_bookmark)
        add_action('📂 목록', "", self.show_bookmarks)
        add_action('🕘 기록', "", self.show_history)

        # 주소창 (여기에 검색 로직이 통합됨)
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("주소 입력 또는 검색 (Ctrl+L)")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        navbar.addWidget(self.url_bar)

        self.log_toggle_btn = QAction('📋 로그', self)
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.triggered.connect(self.toggle_log_window)
        navbar.addAction(self.log_toggle_btn)

        add_action('⚙ 설정', "", self.open_settings)

    def setup_find_bar(self, parent_layout):
        self.find_bar = QWidget()
        row = QHBoxLayout(self.find_bar)
        row.setContentsMargins(6, 3, 6, 3)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("페이지에서 찾기")
        self.find_input.textChanged.connect(lambda text: self.find_text(text))
        self.find_input.returnPressed.connect(lambda: self.find_text(self.find_input.text()))
        row.addWidget(self.find_input)
        for label, backward in (("▲", True), ("▼", False)):
            btn = QPushButton(label)
            btn.setFixedWidth(34)
            btn.clicked.connect(lambda _=False, b=backward: self.find_text(self.find_input.text(), b))
            row.addWidget(btn)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(34)
        close_btn.clicked.connect(self.hide_find_bar)
        row.addWidget(close_btn)
        esc = QShortcut(QKeySequence("Esc"), self.find_bar, activated=self.hide_find_bar)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_bar.hide()
        parent_layout.addWidget(self.find_bar)

    def setup_shortcuts(self):
        def bind(keys, slot):
            QShortcut(QKeySequence(keys), self, activated=slot)

        bind("Ctrl+L", self.focus_url_bar)
        bind("F6", self.focus_url_bar)
        bind("F5", self.reload_page)
        bind("Alt+Left", self.go_back)
        bind("Alt+Right", self.go_forward)
        bind("Ctrl+T", lambda: self.new_tab())
        bind("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex()))
        bind("Ctrl+Tab", lambda: self.switch_tab(1))
        bind("Ctrl+Shift+Tab", lambda: self.switch_tab(-1))
        bind("Ctrl+F", self.show_find_bar)
        bind("Ctrl+=", lambda: self.zoom(0.1))
        bind("Ctrl++", lambda: self.zoom(0.1))
        bind("Ctrl+-", lambda: self.zoom(-0.1))
        bind("Ctrl+0", lambda: self.zoom(None))

    def apply_light_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f6fa; }
            QToolBar { background-color: #ffffff; border-bottom: 1px solid #dcdde1; padding: 5px; spacing: 8px; }
            QLineEdit { background-color: #f1f2f6; color: #2f3640; border: 1px solid #dcdde1; padding: 6px; border-radius: 5px; font-size: 14px; }
            QStatusBar { background-color: #ffffff; color: #2f3640; border-top: 1px solid #dcdde1; }
        """)

    # ---------- 탭 ----------
    def current_view(self):
        return self.tabs.currentWidget()

    def new_tab(self, url=None, switch=True, load=True):
        if self.tabs.count() >= MAX_TABS:
            self.status_bar.showMessage(f"탭은 최대 {MAX_TABS}개까지 열 수 있습니다.", 3000)
            return None
        view = QWebEngineView()
        page = SecurePage(self.profile, view, self.open_tab_for_page)
        view.setPage(page)
        page.permissionRequested.connect(self.on_permission_requested)
        view.urlChanged.connect(lambda q, v=view: self.on_url_changed(v, q))
        view.titleChanged.connect(lambda t, v=view: self.on_title_changed(v, t))
        view.loadProgress.connect(lambda p, v=view: self.on_load_progress(v, p))
        index = self.tabs.addTab(view, "새 탭")
        if switch:
            self.tabs.setCurrentIndex(index)
        if load:
            view.setUrl(QUrl(url or self.settings["home_url"]))
        return view

    def open_tab_for_page(self):
        """페이지가 새 창을 요구할 때 (target=_blank 등) 새 탭의 페이지를 내준다"""
        view = self.new_tab(load=False)
        return view.page() if view else None

    def close_tab(self, index):
        if index < 0:
            return
        if self.tabs.count() == 1:
            self.current_view().setUrl(QUrl(self.settings["home_url"]))
            return
        view = self.tabs.widget(index)
        self.tabs.removeTab(index)
        view.deleteLater()

    def switch_tab(self, step):
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def open_start_tabs(self):
        urls = []
        if self.settings["start_mode"] == "last":
            urls = [u for u in self.session.get("tabs", []) if isinstance(u, str) and u][:MAX_TABS]
        if not urls:
            urls = [self.settings["home_url"]]
        for url in urls:
            self.new_tab(url)
        self.tabs.setCurrentIndex(0)

    def on_tab_changed(self, index):
        view = self.current_view()
        if view is None:
            return
        self.url_bar.setText(view.url().toString())
        self.on_title_changed(view, view.title())
        if self.find_bar.isVisible():
            self.find_text(self.find_input.text())

    # ---------- 페이지 이벤트 ----------
    def on_url_changed(self, view, q):
        url = q.toString()
        if view is self.current_view():
            self.url_bar.setText(url)
        if not url or url == "about:blank" or not self.settings["save_history"]:
            return
        if not self.history or self.history[-1]['url'] != url:
            self.history.append({"url": url, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            self.save_data()

    def on_title_changed(self, view, title):
        index = self.tabs.indexOf(view)
        if index >= 0:
            short = title if len(title) <= 20 else title[:19] + "…"
            self.tabs.setTabText(index, short or "새 탭")
            self.tabs.setTabToolTip(index, title)
        if view is self.current_view():
            self.setWindowTitle(f"{title} - {APP_NAME}" if title else f"{APP_NAME} - 보안 모드")

    def on_load_progress(self, view, percent):
        if view is not self.current_view() or self.detected_blocked():
            return
        if percent < 100:
            self.status_bar.showMessage(f"불러오는 중... {percent}%")
        else:
            self.status_bar.showMessage(self.idle_message())

    def on_download_requested(self, download):
        name = download.downloadFileName()
        if self.settings["allow_downloads"]:
            download.accept()
            self.add_log(f"다운로드 허용: {name}")
        else:
            download.cancel()
            self.add_log(f"⚠️ 다운로드 차단: {name}")
            self.status_bar.showMessage(f"🚫 다운로드가 차단되었습니다: {name}", 5000)

    def on_permission_requested(self, permission):
        # 카메라·마이크·위치 등 권한 요청은 모두 거부
        permission.deny()
        self.add_log(f"⚠️ 권한 요청 거부: {permission.permissionType().name} ({permission.origin().host()})")

    # ---------- 이동 ----------
    def go_back(self):
        view = self.current_view()
        if view:
            view.back()

    def go_forward(self):
        view = self.current_view()
        if view:
            view.forward()

    def reload_page(self):
        view = self.current_view()
        if view:
            view.reload()

    def focus_url_bar(self):
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    # --- 핵심: 스마트 검색/이동 로직 ---
    def navigate_to_url(self):
        input_text = self.url_bar.text().strip()
        view = self.current_view()
        if not input_text or view is None:
            return
        view.setUrl(QUrl(resolve_input(input_text, self.settings["search_engine"])))

    # ---------- 찾기 / 확대·축소 ----------
    def show_find_bar(self):
        self.find_bar.show()
        self.find_input.setFocus()
        self.find_input.selectAll()

    def hide_find_bar(self):
        self.find_bar.hide()
        view = self.current_view()
        if view:
            view.findText("")  # 강조 표시 지우기

    def find_text(self, text, backward=False):
        view = self.current_view()
        if view is None:
            return
        if backward:
            view.findText(text, QWebEnginePage.FindFlag.FindBackward)
        else:
            view.findText(text)

    def zoom(self, delta):
        view = self.current_view()
        if view is None:
            return
        factor = 1.0 if delta is None else min(5.0, max(0.25, round(view.zoomFactor() + delta, 2)))
        view.setZoomFactor(factor)
        self.status_bar.showMessage(f"화면 크기 {int(factor * 100)}%", 2000)

    # ---------- 북마크 / 기록 ----------
    def add_bookmark(self):
        view = self.current_view()
        if view is None:
            return
        url = view.url().toString()
        if any(b['url'] == url for b in self.bookmarks):
            self.status_bar.showMessage("이미 북마크에 있는 주소입니다.", 3000)
            return
        title, ok = QInputDialog.getText(self, "북마크 추가", "이름:", QLineEdit.EchoMode.Normal, view.title())
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
        view = self.current_view()
        if view:
            view.setUrl(QUrl(row.data(Qt.ItemDataRole.UserRole)))
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

    # ---------- 설정 ----------
    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.update(dialog.values())
            self.save_app_settings()
            self.add_log("설정 변경됨")
            self.check_capture_tools()

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
        # 프로그램을 꺼도 남도록 파일에도 기록 (너무 커지면 새 파일로 교체)
        rotate_log(self.log_file)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def flash_alert(self, message, ms=7000):
        self.status_bar.setStyleSheet("background-color: #e74c3c; color: white;")
        self.status_bar.showMessage(message, ms)
        QTimer.singleShot(ms, lambda: self.status_bar.setStyleSheet(""))

    def on_capture_key(self):
        self.add_log("⚠️ 캡처 시도 감지 (PrintScreen)")
        if not self.dock.isVisible():
            self.toggle_log_window()
        self.flash_alert("🚨 보안 경고: 캡처 동작 감지!")
        # 클립보드에 복사된 내용 제거 (캡처 직후 들어오므로 잠시 뒤에 비움)
        QTimer.singleShot(300, QApplication.clipboard().clear)

    def detected_blocked(self):
        return self.stack.currentIndex() == 1

    def check_capture_tools(self):
        action = self.settings["capture_tool_action"]
        found = find_capture_tools() if action != "off" else []
        new = set(found) - self.detected
        gone = self.detected - set(found)
        if new:
            names = ", ".join(sorted(new))
            self.add_log(f"⚠️ 녹화·원격 프로그램 감지: {names}")
            if not self.dock.isVisible():
                self.toggle_log_window()
            self.flash_alert(f"🚨 녹화·원격 프로그램이 실행 중입니다: {names}", 10000)
        if gone:
            self.add_log("녹화·원격 프로그램 종료 확인: " + ", ".join(sorted(gone)))
        self.detected = set(found)
        if action == "block" and found:
            self.cover.setText("🔒 화면 녹화·원격 프로그램이 감지되어 화면을 가렸습니다.\n\n"
                               + ", ".join(found) + "\n\n해당 프로그램을 종료하면 다시 표시됩니다.")
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)

    # ---------- 업데이트 확인 ----------
    def check_for_updates(self, manual=False):
        request = QNetworkRequest(QUrl(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"))
        request.setRawHeader(b"User-Agent", b"YD-Browser")
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self.net.get(request)
        reply.finished.connect(lambda: self.on_update_reply(reply, manual))

    def on_update_reply(self, reply, manual):
        try:
            if reply.error() != reply.NetworkError.NoError:
                if manual:
                    QMessageBox.information(self, "업데이트 확인",
                                            "새 버전 정보를 가져오지 못했습니다.\n"
                                            "인터넷 연결을 확인하거나, 아직 릴리스가 없을 수 있습니다.")
                return
            info = json.loads(bytes(reply.readAll()).decode("utf-8"))
            tag = str(info.get("tag_name", ""))
            page = str(info.get("html_url", ""))
        except Exception:
            if manual:
                QMessageBox.information(self, "업데이트 확인", "새 버전 정보를 읽지 못했습니다.")
            return
        finally:
            reply.deleteLater()

        self.settings["last_update_check"] = datetime.now().strftime("%Y-%m-%d")
        self.save_app_settings()
        if parse_version(tag) > parse_version(VERSION):
            box = QMessageBox(self)
            box.setWindowTitle("새 버전이 있습니다")
            box.setText(f"새 버전 {tag} 이(가) 나왔습니다. (현재 v{VERSION})")
            body = str(info.get("body", "")).strip()
            if body:
                box.setDetailedText(body)
            open_btn = box.addButton("다운로드 페이지 열기", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn and page.startswith("https://github.com/"):
                QDesktopServices.openUrl(QUrl(page))  # 다운로드는 이 브라우저가 아닌 기본 브라우저로
        elif manual:
            QMessageBox.information(self, "업데이트 확인", f"최신 버전입니다. (v{VERSION})")

    # ---------- 창 상태 ----------
    def restore_geometry(self):
        geometry = self.session.get("geometry")
        if isinstance(geometry, str) and geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))

    def closeEvent(self, event):
        self.session["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        if self.settings["save_history"]:
            self.session["tabs"] = [self.tabs.widget(i).url().toString() for i in range(self.tabs.count())
                                    if self.tabs.widget(i).url().toString() not in ("", "about:blank")]
        else:
            self.session.pop("tabs", None)
        self.save_data()
        self.add_log("브라우저 종료")
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_affinity_done", False):
            return
        self._affinity_done = True
        mode = None
        try:
            hwnd = int(self.winId())
            set_affinity = ctypes.windll.user32.SetWindowDisplayAffinity
            # 0x11: 캡처에서 창 자체를 제외 (Windows 10 2004 이상), 안 되면 0x01: 캡처 화면을 검게
            if set_affinity(hwnd, 0x11):
                mode = "창을 캡처에서 제외"
            elif set_affinity(hwnd, 0x01):
                mode = "캡처 화면을 검게 처리"
        except Exception:
            mode = None
        if mode:
            self.add_log(f"화면 캡처 방지 적용됨 ({mode})")
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
