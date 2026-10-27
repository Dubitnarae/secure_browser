# YD Browser (secure_browser)

PyQt6 + QtWebEngine으로 만든 Windows용 보안 브라우저. 현재 버전 v1.7. 개발자 표기: KIM YEONGDO (코드 `AUTHOR`, build.ps1 파일 속성, README, 설정 창). GitHub: Dubitnarae/secure_browser

## 파일
- `secure_browser v1.7.py` — 프로그램 전체 (파일명에 버전을 붙이고, 코드 안 `VERSION`도 같이 올린다. `build.ps1`이 둘이 다르면 중단함)
- `build.ps1` — exe 빌드 + `dist\YD_Browser_v<버전>.zip` + `.sha256` 생성 (`.\build.ps1`로 실행, ASCII만 사용)
- `icon.ico` — 아이콘 (빌드에 포함)
- `test_core.py` — 화면 없이 도는 핵심 기능 시험 (`python test_core.py`, 버전업 때 먼저 실행)
- `README.md`, `CHANGELOG.md` — 버전업 때마다 갱신. 릴리스 설명은 CHANGELOG의 해당 버전 부분을 쓴다
- 실행 중 생기는 개인 데이터(git 제외): `browser_data.json`(암호화), `settings.json`, `security.log*`, `error.log`

## 핵심 동작
- `resolve_input()` — 주소창 입력이 주소인지 검색어인지 판별 (검색어는 URL 인코딩, 검색 엔진은 설정)
- `SetWindowDisplayAffinity` — 0x11(캡처에서 제외) 시도, 실패하면 0x01(검게). 둘 다 실패하면 경고창
- PrintScreen — 앱 전체 이벤트 필터(KeyPress/KeyRelease)로 감지, 로그 + 클립보드 비움
- `dump_store/load_store` — Windows DPAPI 암호화(`YDB1` 머리글). 예전 평문 JSON도 읽음
- `find_capture_tools()` — ToolHelp로 프로세스 목록을 보고 `CAPTURE_TOOLS` 목록과 비교 + 원격 세션 확인. 상주하는 Windows 기본 프로그램(캡처 도구 등)은 오탐 때문에 목록에서 제외함
- 다운로드는 설정 `allow_downloads`(기본 꺼짐)로 차단, 카메라·마이크 등 권한 요청은 모두 거부
- 방문 기록은 최대 `MAX_HISTORY`(1000)개, 저장은 임시 파일에 쓴 뒤 교체
- 업데이트 확인은 GitHub API(`releases/latest`)의 태그를 숫자로 비교, 알림만 하고 다운로드는 기본 브라우저로 연다

## 버전업 절차
1. 파일명(`git mv`)과 `VERSION` 변경
2. 코드 수정, `README.md`·`CHANGELOG.md` 갱신
3. `.\build.ps1` 으로 빌드 → `dist\YD_Browser\YD_Browser.exe` 실행해 확인 (주소 이동, 검색, 탭, 북마크, 기록, 설정)
4. `git add -A` 후 커밋 (메시지에 버전과 변경 사항)
5. 사용자가 push → 태그 `v<버전>` → GitHub Releases에 zip(+sha256) 첨부, 설명은 CHANGELOG에서 붙여 넣기

## 주의
- 사용자는 프로그래밍 초보 — 쉬운 한국어로 설명한다.
- 이 프로젝트는 Synology Drive 동기화 폴더 안에 있으므로, 다른 PC와 동시에 작업하지 않는다.
- exe는 실제로 띄워서 확인할 수 있다 (Start-Process 후 몇 초 뒤 `security.log` 확인). 단 파이썬으로 직접 QApplication을 만드는 시험 스크립트는 이 환경에서 QtWebEngine이 바로 종료됐다.
- 시험으로 exe를 실행하면 `dist\YD_Browser` 안에 데이터 파일이 생기므로, 배포 전에 지운다 (zip은 빌드 때 이미 만들어짐).
- `build.ps1`은 PyInstaller의 stderr 진행 메시지 때문에 그 호출 구간만 ErrorActionPreference를 Continue로 바꿈 (PowerShell 5.1).
- PowerShell 실행 정책 우회(`-ExecutionPolicy Bypass`)는 도구가 거부하므로 `.\build.ps1`로 직접 실행한다.
