"""화면 없이 확인할 수 있는 핵심 기능 시험.  실행: python test_core.py"""
import glob
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
path = sorted(glob.glob(os.path.join(HERE, "secure_browser v*.py")))[-1]
spec = importlib.util.spec_from_file_location("sb", path)
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def test_resolve_input():
    r = sb.resolve_input
    assert r("localhost:8000") == "http://localhost:8000"
    assert r("192.168.0.1:80/x") == "http://192.168.0.1:80/x"
    assert r("naver.com") == "https://naver.com"
    assert r("https://x.com/a") == "https://x.com/a"
    assert r("3.5").startswith("https://www.google.com/search?q=")
    assert r("a.b").startswith("https://www.google.com/search?q=")
    assert r("C++ & C#", "naver") == "https://search.naver.com/search.naver?query=C%2B%2B+%26+C%23"


def test_versions_and_icon():
    assert sb.parse_version("v1.10") > sb.parse_version("1.9")
    assert not sb.parse_version("v1.7") > sb.parse_version(sb.VERSION)
    assert sb.security_icon("https://a.com")[0] == "🔒"
    assert sb.security_icon("http://a.com")[0] == "⚠️"
    assert sb.security_icon("about:blank")[0] == ""


def test_store_encrypts_and_reads_legacy():
    obj = {"bookmarks": [{"title": "한글(제목)", "url": "https://secret.example.com"}], "history": [], "session": {}}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        assert sb.dump_store(p, obj) is True
        raw = open(p, "rb").read()
        assert raw.startswith(sb.MAGIC) and b"secret.example.com" not in raw
        assert sb.load_store(p) == obj
        open(p, "w", encoding="utf-8").write(json.dumps(obj))  # v1.5 이하 평문 파일
        assert sb.load_store(p) == obj


def test_settings_validation():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        sb.save_settings(p, {**sb.DEFAULT_SETTINGS, "search_engine": "zzz", "allow_downloads": True, "save_history": "yes"})
        s = sb.load_settings(p)
        assert s["search_engine"] == "google" and s["allow_downloads"] is True and s["save_history"] is True
        assert sb.load_settings(os.path.join(d, "none.json")) == sb.DEFAULT_SETTINGS


def test_rotate_log():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "log")
        for _ in range(8):
            open(p, "a").write("y" * 60)
            sb.rotate_log(p, max_bytes=100, keep=3)
        names = set(os.listdir(d))
        assert {"log.1", "log.2", "log.3"} <= names and "log.4" not in names  # 지난 파일은 3개까지만


def test_process_scan():
    assert len(sb.running_process_names()) > 5
    assert isinstance(sb.find_capture_tools(), list)


def test_author():
    assert sb.AUTHOR == "KIM YEONGDO"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK  ", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
