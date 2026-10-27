# YD Browser (secure_browser)

PyQt6 + QtWebEngine으로 만든 Windows용 보안 브라우저. 현재 버전 v1.5.

## 파일
- `secure_browser v1.5.py` — 프로그램 전체 (파일명에 버전을 붙이고, 코드 안 `VERSION`도 같이 올린다)
- `browser_data.json` — 북마크·방문 기록 (개인 데이터, git 제외)
- `security.log` — 보안 로그 (git 제외)

## 실행
`python "secure_browser v1.5.py"` (PyQt6, PyQt6-WebEngine 필요)

## 핵심 동작
- `resolve_input()` — 주소창 입력이 주소인지 검색어인지 판별 (검색어는 URL 인코딩 후 구글 검색)
- `SetWindowDisplayAffinity(0x01)` — 화면 캡처 시 검게 나오게 함. 실패하면 경고창
- PrintScreen — 앱 전체 이벤트 필터로 감지, 로그 기록 + 클립보드 비움
- 다운로드는 `ALLOW_DOWNLOADS = False`로 차단, 카메라·마이크 등 권한 요청은 모두 거부
- 방문 기록은 최대 `MAX_HISTORY`(1000)개, 저장은 임시 파일에 쓴 뒤 교체

## 버전업 절차
1. 파일명(`secure_browser v<버전>.py`)과 `VERSION` 변경 (`git mv`로 이름 변경)
2. 실행해서 확인 (주소 이동, 검색, 북마크, 기록, 캡처 키)
3. `git add -A` 후 커밋 (메시지에 버전과 변경 사항)
4. GitHub(Dubitnarae/secure_browser)에 push, 태그 `v<버전>` + 릴리스 설명 작성

## 주의
- 사용자는 프로그래밍 초보 — 쉬운 한국어로 설명한다.
- 이 프로젝트는 Synology Drive 동기화 폴더 안에 있으므로, 다른 PC와 동시에 작업하지 않는다.
- 자동 점검 환경(화면 없는 셸)에서는 QtWebEngine이 바로 종료되어 GUI 실행 확인이 안 된다. GUI는 사용자 PC에서 직접 실행해 확인한다.
