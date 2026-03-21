import sys
import ctypes
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLineEdit, 
                             QPushButton, QVBoxLayout, QHBoxLayout, QWidget, QToolBar)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QAction

class AdvancedSecureBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # 1. 기본 설정 및 창 제목
        self.setWindowTitle("Shield Browser - 보안 모드 활성화")
        self.resize(1200, 800)

        # 2. 메인 위젯 및 레이아웃 설정
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 3. 브라우저 엔진 설정
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("https://www.google.com"))
        
        # 4. 상단 툴바(주소창 및 버튼) 만들기
        self.setup_toolbar()

        # 5. 레이아웃에 툴바와 브라우저 추가
        self.layout.addWidget(self.browser)

        # 6. 다크 모드 스타일 시트 적용
        self.apply_dark_theme()

    def setup_toolbar(self):
        navbar = QToolBar()
        self.addToolBar(navbar)

        # 뒤로 가기 버튼
        back_btn = QAction('◀', self)
        back_btn.triggered.connect(self.browser.back)
        navbar.addAction(back_btn)

        # 앞으로 가기 버튼
        forward_btn = QAction('▶', self)
        forward_btn.triggered.connect(self.browser.forward)
        navbar.addAction(forward_btn)

        # 새로고침 버튼
        reload_btn = QAction('🔄', self)
        reload_btn.triggered.connect(self.browser.reload)
        navbar.addAction(reload_btn)

        # 주소창 (QLineEdit)
        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.navigate_to_url) # 엔터 치면 이동
        navbar.addWidget(self.url_bar)

        # 페이지가 바뀔 때마다 주소창 업데이트
        self.browser.urlChanged.connect(self.update_url)

    def navigate_to_url(self):
        url = self.url_bar.text()
        if not url.startswith('http'):
            url = 'https://' + url
        self.browser.setUrl(QUrl(url))

    def update_url(self, q):
        self.url_bar.setText(q.toString())

    def apply_dark_theme(self):
        """다크 모드 디자인 (QSS) 적용"""
        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; }
            QToolBar { 
                background-color: #333333; 
                border-bottom: 1px solid #444444; 
                padding: 5px;
            }
            QLineEdit { 
                background-color: #1e1e1e; 
                color: #ffffff; 
                border: 1px solid #555555; 
                padding: 5px; 
                border-radius: 4px;
                margin-left: 10px;
            }
            QPushButton, QToolButton { 
                color: #ffffff; 
                font-size: 16px; 
                padding: 5px;
            }
        """)

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_protection()

    def apply_protection(self):
        """Windows API 캡처 차단 적용"""
        try:
            hwnd = int(self.winId())
            WDA_MONITOR = 0x01
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)
        except Exception as e:
            print(f"보안 설정 오류: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AdvancedSecureBrowser()
    window.show()
    sys.exit(app.exec())