import sys
import ctypes
import json
import os
import re
import time
import traceback
from ctypes import wintypes
from urllib.parse import quote_plus
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLineEdit, QVBoxLayout, QHBoxLayout,
                             QWidget, QStatusBar, QTextEdit, QDockWidget, QToolButton, QMenu,
                             QInputDialog, QListWidget, QListWidgetItem, QDialog,
                             QPushButton, QMessageBox, QTabBar, QStackedWidget, QLabel, QProgressBar,
                             QFormLayout, QComboBox, QCheckBox, QDialogButtonBox)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtCore import QUrl, Qt, QObject, QEvent, QTimer, QByteArray, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QIcon, QDesktopServices

APP_NAME = "YD Browser"
VERSION = "1.8"
AUTHOR = "KIM YEONGDO"
GITHUB_REPO = "Dubitnarae/secure_browser"

MAX_HISTORY = 1000          # 방문 기록 최대 보관 개수 (넘으면 오래된 것부터 삭제)
MAX_TABS = 20               # 동시에 열 수 있는 탭 수
BOOKMARK_BAR_MAX = 10       # 북마크바에 보여줄 북마크 수 (나머지는 » 버튼)
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
    "blur_on_inactive": False,
    "show_bookmark_bar": True,
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


def security_icon(url):
    """주소 앞에 보여줄 연결 상태 표시: (아이콘, 설명)"""
    lower = url.lower()
    if lower.startswith("https://"):
        return "🔒", "암호화된 연결(https)입니다."
    if lower.startswith("http://"):
        return "⚠️", "암호화되지 않은 연결(http)입니다. 비밀번호나 개인정보를 입력하지 마세요."
    return "", ""


def install_excepthook(log_path):
    """처리되지 않은 오류를 error.log에 남기고, 프로그램이 갑자기 꺼지지 않게 한다."""
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] v{VERSION}\n{text}\n")
        except OSError:
            pass
        if sys.stderr:
            sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


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


def repolish(widget):
    """속성(property)이 바뀐 뒤 스타일시트를 다시 적용한다"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# 크롬(라이트 테마)과 비슷한 색·모양: 회색 탭 줄, 흰색 주소 줄, 둥근 주소창, 파란 강조색
CHROME_QSS = """
QMainWindow, QDialog { background-color: #ffffff; }
QWidget { font-family: "Segoe UI", "Malgun Gothic", sans-serif; color: #202124; }

QWidget#tabstrip { background-color: #dee1e6; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: #5f6368; height: 34px; min-width: 110px; max-width: 220px;
    padding: 0 6px 0 12px; margin: 0 1px; border-top-left-radius: 9px; border-top-right-radius: 9px; }
QTabBar::tab:selected { background-color: #ffffff; color: #202124; }
QTabBar::tab:!selected:hover { background-color: #eceff3; }
QToolButton#newtab { border: none; border-radius: 14px; background: transparent; color: #5f6368; font-size: 18px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }
QToolButton#newtab:hover { background-color: #cfd2d7; }
QToolButton#tabclose { border: none; border-radius: 9px; background: transparent; color: #5f6368; font-size: 10px;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; }
QToolButton#tabclose:hover { background-color: #cfd2d7; }

QWidget#toolbar { background-color: #ffffff; }
QToolButton#nav { border: none; border-radius: 16px; background: transparent; color: #5f6368; font-size: 17px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; }
QToolButton#nav:hover { background-color: #e8eaed; }
QToolButton#nav:pressed { background-color: #dadce0; }
QToolButton#nav:disabled { color: #c4c7cc; }
QToolButton#nav::menu-indicator { image: none; }
QToolButton#nav[big="true"] { font-size: 24px; font-weight: bold; }

QWidget#omnibox { background-color: #f1f3f4; border: 2px solid transparent; border-radius: 19px; }
QWidget#omnibox:hover { background-color: #e8eaed; }
QWidget#omnibox[omniFocus="true"] { background-color: #ffffff; border: 2px solid #1a73e8; }
QLineEdit#urlbar { background: transparent; border: none; padding: 5px 0; font-size: 14px; color: #202124;
    selection-background-color: #d2e3fc; selection-color: #202124; }
QToolButton#star { border: none; border-radius: 14px; background: transparent; color: #5f6368; font-size: 16px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }
QToolButton#star:hover { background-color: #dadce0; }
QToolButton#star[marked="true"] { color: #1a73e8; }

QWidget#bmbar { background-color: #ffffff; }
QToolButton#bm { border: none; border-radius: 4px; padding: 4px 8px; color: #3c4043; font-size: 12px; background: transparent; }
QToolButton#bm:hover { background-color: #f1f3f4; }
QProgressBar#progress { background-color: #dadce0; border: none; }
QProgressBar#progress::chunk { background-color: #1a73e8; }

QWidget#findbar { background-color: #ffffff; border-top: 1px solid #dadce0; }
QLineEdit { background-color: #ffffff; border: 1px solid #dadce0; border-radius: 6px; padding: 6px; }
QLineEdit:focus { border: 2px solid #1a73e8; padding: 5px; }
QPushButton { background-color: #ffffff; border: 1px solid #dadce0; border-radius: 16px; color: #1a73e8; padding: 6px 16px; }
QPushButton:hover { background-color: #f6fafe; border-color: #d2e3fc; }
QPushButton:pressed { background-color: #e8f0fe; }
QListWidget { border: 1px solid #dadce0; border-radius: 6px; }
QListWidget::item { padding: 6px; }
QListWidget::item:selected { background-color: #e8f0fe; color: #202124; }

QMenu { background-color: #ffffff; border: 1px solid #dadce0; padding: 6px 0; }
QMenu::item { padding: 7px 28px 7px 24px; color: #202124; }
QMenu::item:selected { background-color: #f1f3f4; }
QMenu::separator { height: 1px; background-color: #e8eaed; margin: 6px 0; }
QToolTip { background-color: #3c4043; color: #ffffff; border: none; padding: 4px 8px; }
QStatusBar { background-color: #f1f3f4; color: #3c4043; border-top: 1px solid #dadce0; }
"""


class TabHost(QObject):
    """크롬처럼 주소 줄 '위'에 놓이는 탭 줄. 탭마다 웹 화면(view)을 하나씩 가진다.
    (QTabWidget은 탭 줄과 화면 사이에 주소 줄을 끼울 수 없어서 직접 만들었다)"""
    currentChanged = pyqtSignal(int)
    tabCloseRequested = pyqtSignal(int)
    newTabRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.views = []
        self.stack = QStackedWidget()
        self.bar = QTabBar()
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setDocumentMode(True)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setIconSize(QSize(16, 16))
        self.bar.currentChanged.connect(self._on_current)
        self.bar.tabMoved.connect(self._on_moved)

        new_btn = QToolButton()
        new_btn.setObjectName("newtab")
        new_btn.setText("+")
        new_btn.setToolTip("새 탭 (Ctrl+T)")
        new_btn.clicked.connect(lambda _=False: self.newTabRequested.emit())

        self.strip = QWidget()
        self.strip.setObjectName("tabstrip")
        self.strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self.strip)
        row.setContentsMargins(8, 6, 8, 0)
        row.setSpacing(2)
        row.addWidget(self.bar)
        row.addWidget(new_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

    def _on_current(self, index):
        if 0 <= index < len(self.views):
            self.stack.setCurrentWidget(self.views[index])
            self.currentChanged.emit(index)

    def _on_moved(self, src, dst):
        self.views.insert(dst, self.views.pop(src))

    def count(self):
        return len(self.views)

    def widget(self, index):
        return self.views[index] if 0 <= index < len(self.views) else None

    def indexOf(self, view):
        return self.views.index(view) if view in self.views else -1

    def currentIndex(self):
        return self.bar.currentIndex()

    def currentWidget(self):
        return self.widget(self.bar.currentIndex())

    def setCurrentIndex(self, index):
        self.bar.setCurrentIndex(index)

    def addTab(self, view, text):
        self.views.append(view)
        self.stack.addWidget(view)
        index = self.bar.addTab(text)
        close_btn = QToolButton()
        close_btn.setObjectName("tabclose")
        close_btn.setText("✕")
        close_btn.setToolTip("탭 닫기 (Ctrl+W)")
        close_btn.clicked.connect(lambda _=False, v=view: self.tabCloseRequested.emit(self.indexOf(v)))
        self.bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close_btn)
        return index

    def removeTab(self, index):
        view = self.views.pop(index)
        self.stack.removeWidget(view)
        self.bar.removeTab(index)

    def setTabText(self, index, text):
        if index >= 0:
            self.bar.setTabText(index, text)

    def setTabToolTip(self, index, text):
        if index >= 0:
            self.bar.setTabToolTip(index, text)

    def setTabIcon(self, index, icon):
        if index >= 0:
            self.bar.setTabIcon(index, icon)


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

        self.blur_on_inactive = QCheckBox("다른 창으로 전환하면 화면 가리기")
        self.blur_on_inactive.setChecked(s["blur_on_inactive"])
        form.addRow("화면 보호", self.blur_on_inactive)

        self.check_updates = QCheckBox("하루 한 번 새 버전 확인 (GitHub에 접속)")
        self.check_updates.setChecked(s["check_updates"])
        form.addRow("업데이트", self.check_updates)

        update_btn = QPushButton("지금 새 버전 확인")
        update_btn.clicked.connect(lambda: window.check_for_updates(manual=True))
        folder_btn = QPushButton("저장 폴더 열기 (설정·로그 위치)")
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(BASE_DIR)))
        form.addRow("", update_btn)
        form.addRow("", folder_btn)

        info = QLabel(f"{APP_NAME} v{VERSION}\n개발자: {AUTHOR}")
        info.setStyleSheet("color: #636e72;")
        form.addRow(info)

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
            "blur_on_inactive": self.blur_on_inactive.isChecked(),
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
        self.capture_cover_text = ""   # 녹화 프로그램 때문에 가릴 때의 안내 글
        self.inactive_cover = False    # 다른 창으로 전환해서 가리는 중인지
        self.load_data()

        # --- 화면: 크롬처럼 [탭 줄] [주소 줄] [북마크바] [진행 표시] [웹 화면] [찾기 줄]
        self.stack = QStackedWidget()          # 0: 브라우저 화면, 1: 가림막
        self.setCentralWidget(self.stack)
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.tabs = TabHost(self)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.newTabRequested.connect(lambda: self.new_tab())
        main_layout.addWidget(self.tabs.strip)
        main_layout.addWidget(self.build_toolbar())
        main_layout.addWidget(self.build_bookmark_bar())
        self.progress = QProgressBar()
        self.progress.setObjectName("progress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(2)
        main_layout.addWidget(self.progress)
        main_layout.addWidget(self.tabs.stack, 1)
        self.setup_find_bar(main_layout)
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setWordWrap(True)
        self.cover.setStyleSheet("background-color: #202124; color: white; font-size: 18px; padding: 40px;")
        self.stack.addWidget(main_page)
        self.stack.addWidget(self.cover)

        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setStyleSheet("background-color: #ffffff; color: #2c3e50; font-family: Consolas; border: none;")
        self.dock = QDockWidget("Security Logs", self)
        self.dock.setWidget(self.log_viewer)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()

        # 상태 표시줄은 크롬처럼 평소에는 숨기고, 알림이 있을 때만 보인다
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.hide()
        self.status_bar.messageChanged.connect(lambda text: self.status_bar.setVisible(bool(text)))

        self.setup_shortcuts()
        self.apply_chrome_theme()
        self.refresh_bookmark_bar()

        # --- 보안 정책 (다운로드 차단은 프로필, 권한 거부는 페이지마다)
        self.profile = QWebEngineProfile.defaultProfile()
        self.profile.downloadRequested.connect(self.on_download_requested)

        self.tabs.currentChanged.connect(self.on_tab_changed)
        QApplication.instance().applicationStateChanged.connect(self.on_app_state)
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
    def build_toolbar(self):
        """뒤로·앞으로·새로고침 / 둥근 주소창(연결 상태, 주소, 북마크 별) / 메뉴(⋮)"""
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(4)

        def tool_button(text, tip, slot, name="nav"):
            button = QToolButton()
            button.setObjectName(name)
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False: slot())
            return button

        self.back_btn = tool_button("←", "뒤로 (Alt+←)", self.go_back)
        self.forward_btn = tool_button("→", "앞으로 (Alt+→)", self.go_forward)
        self.reload_btn = tool_button("⟳", "새로고침 (F5)", self.reload_or_stop)
        for button in (self.back_btn, self.forward_btn, self.reload_btn):
            row.addWidget(button)

        # 주소창 (여기에 검색 로직이 통합됨)
        self.omnibox = QWidget()
        self.omnibox.setObjectName("omnibox")
        self.omnibox.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.omnibox.setMinimumHeight(38)
        inner = QHBoxLayout(self.omnibox)
        inner.setContentsMargins(12, 0, 6, 0)
        inner.setSpacing(6)
        self.sec_label = QLabel()
        inner.addWidget(self.sec_label)
        self.url_bar = QLineEdit()
        self.url_bar.setObjectName("urlbar")
        self.url_bar.setPlaceholderText("주소 입력 또는 검색 (Ctrl+L)")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        self.url_bar.installEventFilter(self)
        inner.addWidget(self.url_bar, 1)
        self.star_btn = tool_button("☆", "이 페이지를 북마크 (Ctrl+D)", self.toggle_bookmark, "star")
        inner.addWidget(self.star_btn)
        row.addWidget(self.omnibox, 1)

        menu_btn = QToolButton()
        menu_btn.setObjectName("nav")
        menu_btn.setText("⋮")
        menu_btn.setProperty("big", True)
        menu_btn.setToolTip("메뉴")
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setMenu(self.build_menu())
        row.addWidget(menu_btn)
        return bar

    def build_menu(self):
        menu = QMenu(self)

        def add(text, slot, checkable=False):
            action = menu.addAction(text)
            action.setCheckable(checkable)
            action.triggered.connect(lambda _checked=False: slot())
            return action

        add("새 탭\tCtrl+T", lambda: self.new_tab())
        menu.addSeparator()
        add("북마크 목록", self.show_bookmarks)
        self.bm_bar_action = add("북마크바 표시\tCtrl+Shift+B", self.toggle_bookmark_bar, True)
        self.bm_bar_action.setChecked(self.settings["show_bookmark_bar"])
        add("방문 기록\tCtrl+H", self.show_history)
        menu.addSeparator()
        add("페이지에서 찾기\tCtrl+F", self.show_find_bar)
        add("확대\tCtrl++", lambda: self.zoom(0.1))
        add("축소\tCtrl+-", lambda: self.zoom(-0.1))
        add("원래 크기\tCtrl+0", lambda: self.zoom(None))
        menu.addSeparator()
        self.log_toggle_btn = add("보안 로그", self.toggle_log_window, True)
        add("설정", self.open_settings)
        return menu

    def build_bookmark_bar(self):
        self.bm_bar = QWidget()
        self.bm_bar.setObjectName("bmbar")
        self.bm_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bm_row = QHBoxLayout(self.bm_bar)
        self.bm_row.setContentsMargins(8, 2, 8, 3)
        self.bm_row.setSpacing(2)
        return self.bm_bar

    def refresh_bookmark_bar(self):
        """북마크바를 현재 북마크로 다시 만든다 (없거나 끄면 숨김)"""
        while self.bm_row.count():
            item = self.bm_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()           # deleteLater만 하면 새 버튼 뒤에 잠깐 남아 글자가 겹쳐 보인다
                widget.setParent(None)
                widget.deleteLater()
        if not self.settings["show_bookmark_bar"] or not self.bookmarks:
            self.bm_bar.hide()
            return
        for bookmark in self.bookmarks[:BOOKMARK_BAR_MAX]:
            title = bookmark["title"]
            button = QToolButton()
            button.setObjectName("bm")
            button.setText(title if len(title) <= 18 else title[:17] + "…")
            button.setToolTip(f"{title}\n{bookmark['url']}")
            button.clicked.connect(lambda _=False, b=bookmark: self.open_bookmark(b))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, btn=button, b=bookmark: self.bookmark_context_menu(btn, b, pos))
            self.bm_row.addWidget(button)
        if len(self.bookmarks) > BOOKMARK_BAR_MAX:
            more = QToolButton()
            more.setObjectName("bm")
            more.setText("»")
            more.setToolTip("북마크 전체 보기")
            more.clicked.connect(lambda _=False: self.show_bookmarks())
            self.bm_row.addWidget(more)
        self.bm_row.addStretch(1)
        self.bm_bar.show()

    def open_bookmark(self, bookmark, new_tab=False):
        if new_tab:
            self.new_tab(bookmark["url"])
        else:
            view = self.current_view()
            if view:
                view.setUrl(QUrl(bookmark["url"]))

    def bookmark_context_menu(self, button, bookmark, pos):
        menu = QMenu(self)
        open_action = menu.addAction("열기")
        new_tab_action = menu.addAction("새 탭에서 열기")
        menu.addSeparator()
        delete_action = menu.addAction("삭제")
        chosen = menu.exec(button.mapToGlobal(pos))
        if chosen is open_action:
            self.open_bookmark(bookmark)
        elif chosen is new_tab_action:
            self.open_bookmark(bookmark, new_tab=True)
        elif chosen is delete_action:
            self.remove_bookmark_url(bookmark["url"])

    def setup_find_bar(self, parent_layout):
        self.find_bar = QWidget()
        self.find_bar.setObjectName("findbar")
        self.find_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self.find_bar)
        row.setContentsMargins(10, 6, 10, 6)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("페이지에서 찾기")
        self.find_input.textChanged.connect(lambda text: self.find_text(text))
        self.find_input.returnPressed.connect(lambda: self.find_text(self.find_input.text()))
        row.addWidget(self.find_input)
        for label, backward in (("▲", True), ("▼", False)):
            btn = QPushButton(label)
            btn.setFixedWidth(40)
            btn.clicked.connect(lambda _=False, b=backward: self.find_text(self.find_input.text(), b))
            row.addWidget(btn)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(40)
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
        bind("Ctrl+D", self.toggle_bookmark)
        bind("Ctrl+H", self.show_history)
        bind("Ctrl+Shift+B", self.toggle_bookmark_bar)
        bind("Ctrl+=", lambda: self.zoom(0.1))
        bind("Ctrl++", lambda: self.zoom(0.1))
        bind("Ctrl+-", lambda: self.zoom(-0.1))
        bind("Ctrl+0", lambda: self.zoom(None))

    def apply_chrome_theme(self):
        self.setStyleSheet(CHROME_QSS)

    def eventFilter(self, obj, event):
        # 주소창에 포커스가 가면 크롬처럼 파란 테두리를 보이고 전체 선택한다
        if obj is self.url_bar:
            if event.type() == QEvent.Type.FocusIn:
                self.set_omnibox_focus(True)
                QTimer.singleShot(0, self.url_bar.selectAll)
            elif event.type() == QEvent.Type.FocusOut:
                self.set_omnibox_focus(False)
        return super().eventFilter(obj, event)

    def set_omnibox_focus(self, on):
        self.omnibox.setProperty("omniFocus", on)
        repolish(self.omnibox)

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
        view.iconChanged.connect(lambda icon, v=view: self.tabs.setTabIcon(self.tabs.indexOf(v), icon))
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
        self.show_url(view.url().toString())
        self.on_title_changed(view, view.title())
        self.progress.setValue(0)
        self.update_reload_button()
        self.update_nav_buttons()
        if self.find_bar.isVisible():
            self.find_text(self.find_input.text())

    # ---------- 페이지 이벤트 ----------
    def on_url_changed(self, view, q):
        url = q.toString()
        if view is self.current_view():
            self.show_url(url)
            self.update_nav_buttons()
        if not url or url == "about:blank" or not self.settings["save_history"]:
            return
        if not self.history or self.history[-1]['url'] != url:
            self.history.append({"url": url, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            self.save_data()

    def show_url(self, url):
        self.url_bar.setText(url)
        icon, tip = security_icon(url)
        self.sec_label.setText(icon)
        self.sec_label.setToolTip(tip)
        self.update_star(url)

    def update_star(self, url):
        marked = any(b['url'] == url for b in self.bookmarks)
        self.star_btn.setText("★" if marked else "☆")
        self.star_btn.setToolTip("북마크 삭제 (Ctrl+D)" if marked else "이 페이지를 북마크 (Ctrl+D)")
        self.star_btn.setProperty("marked", marked)
        repolish(self.star_btn)

    def update_nav_buttons(self):
        view = self.current_view()
        history = view.history() if view else None
        self.back_btn.setEnabled(bool(history and history.canGoBack()))
        self.forward_btn.setEnabled(bool(history and history.canGoForward()))

    def update_reload_button(self):
        view = self.current_view()
        loading = bool(view is not None and getattr(view, "_loading", False))
        self.reload_btn.setText("✕" if loading else "⟳")
        self.reload_btn.setToolTip("로드 중지" if loading else "새로고침 (F5)")

    def on_title_changed(self, view, title):
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, title or "새 탭")   # 길면 탭 줄이 알아서 줄여서 보여준다
            self.tabs.setTabToolTip(index, title)
        if view is self.current_view():
            self.setWindowTitle(f"{title} - {APP_NAME}" if title else f"{APP_NAME} - 보안 모드")

    def on_load_progress(self, view, percent):
        view._loading = percent < 100
        if view is not self.current_view():
            return
        self.progress.setValue(percent if percent < 100 else 0)
        self.update_reload_button()
        if percent >= 100:
            self.update_nav_buttons()

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

    def reload_or_stop(self):
        """불러오는 중이면 중지, 아니면 새로고침 (크롬의 ⟳ / ✕ 버튼)"""
        view = self.current_view()
        if view is None:
            return
        if getattr(view, "_loading", False):
            view.stop()
        else:
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
            self.bookmarks_changed()
            self.status_bar.showMessage("북마크에 추가했습니다.", 3000)

    def toggle_bookmark(self):
        """주소창의 별(☆/★), Ctrl+D: 없으면 추가, 이미 있으면 삭제"""
        view = self.current_view()
        if view is None:
            return
        url = view.url().toString()
        if any(b['url'] == url for b in self.bookmarks):
            self.remove_bookmark_url(url)
        else:
            self.add_bookmark()

    def remove_bookmark_url(self, url):
        self.bookmarks = [b for b in self.bookmarks if b['url'] != url]
        self.save_data()
        self.bookmarks_changed()
        self.status_bar.showMessage("북마크를 삭제했습니다.", 3000)

    def bookmarks_changed(self):
        self.refresh_bookmark_bar()
        view = self.current_view()
        if view:
            self.update_star(view.url().toString())

    def toggle_bookmark_bar(self):
        self.settings["show_bookmark_bar"] = not self.settings["show_bookmark_bar"]
        self.bm_bar_action.setChecked(self.settings["show_bookmark_bar"])
        self.save_app_settings()
        self.refresh_bookmark_bar()

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
        self.bookmarks_changed()
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
            self.on_app_state(QApplication.applicationState())

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
        self.status_bar.setStyleSheet("QStatusBar { background-color: #d93025; color: white; border: none; }")
        self.status_bar.showMessage(message, ms)
        QTimer.singleShot(ms, lambda: self.status_bar.setStyleSheet(""))

    def on_capture_key(self):
        self.add_log("⚠️ 캡처 시도 감지 (PrintScreen)")
        if not self.dock.isVisible():
            self.toggle_log_window()
        self.flash_alert("🚨 보안 경고: 캡처 동작 감지!")
        # 클립보드에 복사된 내용 제거 (캡처 직후 들어오므로 잠시 뒤에 비움)
        QTimer.singleShot(300, QApplication.clipboard().clear)

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
            self.capture_cover_text = ("🔒 화면 녹화·원격 프로그램이 감지되어 화면을 가렸습니다.\n\n"
                                       + ", ".join(found) + "\n\n해당 프로그램을 종료하면 다시 표시됩니다.")
        else:
            self.capture_cover_text = ""
        self.update_cover()

    def on_app_state(self, state):
        self.inactive_cover = (self.settings["blur_on_inactive"]
                               and state != Qt.ApplicationState.ApplicationActive)
        self.update_cover()

    def update_cover(self):
        """가림막: 녹화 프로그램 감지가 우선, 그다음 다른 창으로 전환했을 때"""
        text = self.capture_cover_text
        if not text and self.inactive_cover:
            text = "🔒 다른 창으로 전환되어 화면을 가렸습니다.\n\n이 창을 클릭하면 다시 표시됩니다."
        if text:
            self.cover.setText(text)
        self.stack.setCurrentIndex(1 if text else 0)

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
    install_excepthook(os.path.join(BASE_DIR, "error.log"))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(AUTHOR)
    window = LoggingSecureBrowser()
    key_filter = CaptureKeyFilter(window.on_capture_key)
    app.installEventFilter(key_filter)
    window.show()
    sys.exit(app.exec())
