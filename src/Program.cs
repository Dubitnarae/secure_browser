// YD Browser - 화면 캡처를 막고 보안 로그를 남기는 Windows 용 브라우저 (C# + Microsoft Edge WebView2)
// 실행: YD_Browser.exe, 자체 시험: YD_Browser.exe --selftest (빌드 때 실행)

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

[assembly: System.Reflection.AssemblyTitle("YD Browser")]
[assembly: System.Reflection.AssemblyProduct("YD Browser")]
[assembly: System.Reflection.AssemblyCompany("KIM YEONGDO")]
[assembly: System.Reflection.AssemblyCopyright("Copyright (c) 2026 KIM YEONGDO")]
[assembly: System.Reflection.AssemblyVersion("2.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("2.0.0.0")]

namespace YDBrowser
{
    static class App
    {
        public const string Name = "YD Browser";
        public const string Author = "KIM YEONGDO";
        public const string Repo = "Dubitnarae/secure_browser";

        // "2.0" 처럼 (세 번째 숫자는 0 이 아닐 때만)
        public static string Version
        {
            get
            {
                var v = typeof(App).Assembly.GetName().Version;
                return v.Major + "." + v.Minor + (v.Build > 0 ? "." + v.Build : "");
            }
        }

        // 데이터 파일은 실행 위치와 상관없이 프로그램이 있는 폴더에 저장
        public static string BaseDir { get { return Path.GetDirectoryName(Application.ExecutablePath); } }
    }

    static class Program
    {
        [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int v);
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

        [STAThread]
        static int Main(string[] args)
        {
            try { SetProcessDpiAwareness(1); }   // 화면 배율에 맞춰 선명하게
            catch (Exception) { try { SetProcessDPIAware(); } catch (Exception) { } }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Contains("--selftest")) return SelfTest.Run();
            // 처리되지 않은 오류는 error.log 에 남기고, 프로그램이 갑자기 꺼지지 않게 한다
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += (s, e) => Core.LogError(e.Exception);
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Core.LogError(e.ExceptionObject as Exception);
            Application.Run(new MainForm());
            return 0;
        }
    }

    // 자체 시험 — 파이썬판 test_core.py 를 옮긴 것. 실패하면 build.ps1 이 빌드를 막는다
    static class SelfTest
    {
        static int fails;
        static readonly StringBuilder log = new StringBuilder();

        static void Check(bool ok, string what)
        {
            if (!ok) fails++;
            log.AppendLine((ok ? "  ok   " : "  FAIL ") + what);
        }

        public static int Run()
        {
            var tests = new Dictionary<string, Action>
            {
                { "주소창 입력 해석", Resolve }, { "버전·연결 표시", VersionsAndIcon }, { "암호화 저장", Store },
                { "설정 검사", SettingsCheck }, { "로그 교체", Rotate }, { "프로그램 감지", ProcessScan },
                { "웹 엔진", Engine }, { "화면·단축키", Window },
            };
            foreach (var t in tests)
            {
                log.AppendLine("[" + t.Key + "]");
                try { t.Value(); }
                catch (Exception e) { Check(false, "예외: " + e); }
            }
            log.AppendLine(fails == 0 ? "SELFTEST OK" : "SELFTEST FAILED: " + fails);
            var bytes = Encoding.UTF8.GetBytes(log.ToString());   // 창 프로그램이라 콘솔 인코딩을 못 바꾼다 — 바이트로 직접
            using (var o = Console.OpenStandardOutput()) o.Write(bytes, 0, bytes.Length);
            return fails == 0 ? 0 : 1;
        }

        static string TempDir()
        {
            string d = Path.Combine(Path.GetTempPath(), "yd_browser_selftest_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(d);
            return d;
        }

        static void Resolve()
        {
            Func<string, string> r = s => Core.ResolveInput(s);
            Check(r("localhost:8000") == "http://localhost:8000", "localhost 는 http");
            Check(r("192.168.0.1:80/x") == "http://192.168.0.1:80/x", "IP 는 http");
            Check(r("naver.com") == "https://naver.com", "도메인은 https");
            Check(r("https://x.com/a") == "https://x.com/a", "주소 그대로");
            Check(r("3.5").StartsWith("https://www.google.com/search?q="), "숫자는 검색");
            Check(r("a.b").StartsWith("https://www.google.com/search?q="), "한 글자 끝은 검색");
            Check(Core.ResolveInput("C++ & C#", "naver") == "https://search.naver.com/search.naver?query=C%2B%2B+%26+C%23", "검색어 인코딩");
            Check(r("한글 검색") == "https://www.google.com/search?q=%ED%95%9C%EA%B8%80+%EA%B2%80%EC%83%89", "한글 검색어");
        }

        static void VersionsAndIcon()
        {
            Check(Core.IsNewer("v1.10", "1.9") && Core.IsNewer("v2.0", "1.9") && !Core.IsNewer("v1.7", App.Version), "버전 비교");
            Check(App.Version == "2.0", "버전 표기 " + App.Version);
            Check(Core.SecurityIcon("https://a.com")[0] == "🔒" && Core.SecurityIcon("http://a.com")[0] == "⚠️" && Core.SecurityIcon("about:blank")[0] == "", "연결 표시");
            Check(App.Author == "KIM YEONGDO", "개발자 표기");
        }

        static void Store()
        {
            string d = TempDir(), p = Path.Combine(d, "x.json");
            var obj = new Dictionary<string, object>
            {
                { "bookmarks", new[] { new Dictionary<string, object> { { "title", "한글(제목)" }, { "url", "https://secret.example.com" } } } },
                { "history", new object[0] }, { "session", new Dictionary<string, object>() },
            };
            Check(Core.DumpStore(p, obj), "암호화 성공");
            byte[] raw = File.ReadAllBytes(p);
            Check(Encoding.ASCII.GetString(raw, 0, 4) == "YDB1" && !Encoding.UTF8.GetString(raw).Contains("secret.example.com"), "파일에 주소가 보이지 않음");
            Func<Dictionary<string, object>, string> title = x => (string)((Dictionary<string, object>)((object[])x["bookmarks"])[0])["title"];
            Check(title(Core.LoadStore(p)) == "한글(제목)", "다시 읽기");
            File.WriteAllText(p, Core.Js.Serialize(obj), new UTF8Encoding(true));   // v1.5 이하 평문 파일 (BOM 포함)
            Check(title(Core.LoadStore(p)) == "한글(제목)", "예전 평문 파일 읽기");
            File.WriteAllBytes(p, Encoding.ASCII.GetBytes("YDB1garbage"));
            bool threw = false;
            try { Core.LoadStore(p); } catch (Exception) { threw = true; }
            Check(threw, "깨진 파일은 예외");
            Directory.Delete(d, true);
        }

        static void SettingsCheck()
        {
            string d = TempDir(), p = Path.Combine(d, "s.json");
            var s = Core.DefaultSettings();
            s["search_engine"] = "zzz"; s["allow_downloads"] = true; s["save_history"] = "yes";
            Core.SaveSettings(p, s);
            var l = Core.LoadSettings(p);
            Check((string)l["search_engine"] == "google" && (bool)l["allow_downloads"] && (bool)l["save_history"], "잘못된 값은 기본값");
            var none = Core.LoadSettings(Path.Combine(d, "none.json"));
            Check(Core.Js.Serialize(none) == Core.Js.Serialize(Core.DefaultSettings()), "파일 없으면 기본값");
            // 파이썬판이 쓴 settings.json (들여쓰기·한글)
            File.WriteAllText(p, "{\n    \"home_url\": \"https://www.naver.com\",\n    \"start_mode\": \"last\",\n    \"blur_on_inactive\": true\n}", Encoding.UTF8);
            l = Core.LoadSettings(p);
            Check((string)l["home_url"] == "https://www.naver.com" && (string)l["start_mode"] == "last" && (bool)l["blur_on_inactive"], "파이썬판 설정 읽기");
            Directory.Delete(d, true);
        }

        static void Rotate()
        {
            string d = TempDir(), p = Path.Combine(d, "log");
            for (int i = 0; i < 8; i++)
            {
                File.AppendAllText(p, new string('y', 60));
                Core.RotateLog(p, 100, 3);
            }
            var names = new HashSet<string>(Directory.GetFiles(d).Select(Path.GetFileName));
            Check(names.Contains("log.1") && names.Contains("log.2") && names.Contains("log.3") && !names.Contains("log.4"), "지난 파일은 3개까지만");
            Directory.Delete(d, true);
        }

        static void ProcessScan()
        {
            Check(Core.RunningProcessNames().Count > 5, "프로세스 목록");
            Check(Core.FindCaptureTools() != null, "감지 목록");
        }

        static void Engine()
        {
            string v = null;
            try { v = CoreWebView2Environment.GetAvailableBrowserVersionString(); } catch (Exception) { }
            Check(!string.IsNullOrEmpty(v), "WebView2 런타임 " + v);
        }

        static void Window()
        {
            using (var f = new MainForm())
            {
                var h = f.Handle;
                Check(f.HasShortcut(Keys.Control | Keys.T) && f.HasShortcut(Keys.Control | Keys.Shift | Keys.T) && f.HasShortcut(Keys.F5)
                    && f.HasShortcut(Keys.Control | Keys.D9) && f.HasShortcut(Keys.Alt | Keys.Left), "단축키 연결");
                Check(!f.HasShortcut(Keys.Control | Keys.S) && !f.HasShortcut(Keys.Control | Keys.P) && !f.HasShortcut(Keys.Control | Keys.U), "저장·인쇄·소스 보기 없음");
                Check(Native.ProtectWindow(h) != null, "캡처 방지 적용");
            }
        }
    }
}
