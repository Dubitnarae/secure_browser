// 화면과 무관한 부분: 설정, 주소창 입력 해석, 암호화 저장, 녹화·원격 프로그램 감지, 로그 파일

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace YDBrowser
{
    static class Core
    {
        public const int MaxHistory = 1000;          // 방문 기록 최대 보관 개수 (넘으면 오래된 것부터 삭제)
        public const int MaxTabs = 20;               // 동시에 열 수 있는 탭 수
        public const int BookmarkBarMax = 10;        // 북마크바에 보여줄 북마크 수 (나머지는 » 버튼)
        public const int ClosedTabsMax = 10;         // '닫은 탭 다시 열기'로 기억할 탭 수
        public const int SuggestHistoryMax = 300;    // 주소창 추천에 쓸 최근 방문 기록 수
        public const long LogMaxBytes = 1000000;     // 보안 로그가 이 크기를 넘으면 새 파일로 교체
        public const int LogKeep = 3;                // 보관할 지난 로그 파일 개수
        public const int CaptureCheckMs = 5000;      // 녹화·원격 프로그램 확인 주기

        public static readonly JavaScriptSerializer Js = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        // ---------------------------------------------------------------- 설정
        public static readonly Dictionary<string, string[]> SearchEngines = new Dictionary<string, string[]>
        {
            { "google", new[] { "구글", "https://www.google.com/search?q=" } },
            { "naver", new[] { "네이버", "https://search.naver.com/search.naver?query=" } },
            { "bing", new[] { "빙", "https://www.bing.com/search?q=" } },
            { "duckduckgo", new[] { "덕덕고", "https://duckduckgo.com/?q=" } },
        };
        public static readonly Dictionary<string, string> CaptureActions = new Dictionary<string, string>
        {
            { "off", "확인 안 함" }, { "warn", "경고만" }, { "block", "화면 가리기" },
        };
        public static readonly Dictionary<string, string> StartModes = new Dictionary<string, string>
        {
            { "home", "시작 페이지" }, { "last", "마지막 탭 복원" },
        };

        public static Dictionary<string, object> DefaultSettings()
        {
            return new Dictionary<string, object>
            {
                { "home_url", "https://www.google.com" }, { "search_engine", "google" }, { "start_mode", "home" },
                { "allow_downloads", false }, { "save_history", true }, { "capture_tool_action", "warn" },
                { "blur_on_inactive", false }, { "show_bookmark_bar", true }, { "check_updates", true },
                { "last_update_check", "" },
            };
        }

        // 설정 파일(파이썬판과 같은 settings.json)을 읽는다. 종류가 맞지 않는 값은 기본값을 쓴다.
        public static Dictionary<string, object> LoadSettings(string path)
        {
            var s = DefaultSettings();
            try
            {
                var data = Js.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>;
                if (data != null)
                    foreach (var key in s.Keys.ToList())
                    {
                        object v;
                        if (data.TryGetValue(key, out v) && v != null && v.GetType() == s[key].GetType()) s[key] = v;
                    }
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is ArgumentException || e is InvalidOperationException || e is UnauthorizedAccessException)) throw;
            }
            if (!SearchEngines.ContainsKey((string)s["search_engine"])) s["search_engine"] = "google";
            if (!CaptureActions.ContainsKey((string)s["capture_tool_action"])) s["capture_tool_action"] = "warn";
            if (!StartModes.ContainsKey((string)s["start_mode"])) s["start_mode"] = "home";
            return s;
        }

        public static void SaveSettings(string path, Dictionary<string, object> s)
        {
            WriteReplace(path, new UTF8Encoding(false).GetBytes(Js.Serialize(s)));
        }

        // 임시 파일에 쓴 뒤 교체 → 쓰는 도중 종료돼도 원본이 깨지지 않음
        public static void WriteReplace(string path, byte[] bytes)
        {
            string tmp = path + ".tmp";
            File.WriteAllBytes(tmp, bytes);
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
        }

        // ---------------------------------------------------------------- 주소창 입력 해석
        // 도메인(또는 localhost, IPv4) + 선택적 포트/경로
        static readonly Regex UrlRe = new Regex(@"^(localhost|(\d{1,3}\.){3}\d{1,3}|([\w-]+\.)+[^\W\d_]{2,})(:\d+)?([/?#].*)?$", RegexOptions.IgnoreCase);
        static readonly Regex LocalRe = new Regex(@"^(localhost|(\d{1,3}\.){3}\d{1,3})(:\d+)?([/?#]|$)", RegexOptions.IgnoreCase);
        static readonly Regex SchemeRe = new Regex(@"^(https?|file)://", RegexOptions.IgnoreCase);

        public static bool HasScheme(string text) { return SchemeRe.IsMatch(text); }

        // 주소창 입력을 이동할 URL로 바꾼다. 주소가 아니면 검색 주소를 돌려준다.
        public static string ResolveInput(string text, string engine = "google")
        {
            text = text.Trim();
            if (HasScheme(text)) return text;
            if (!text.Contains(' ') && UrlRe.IsMatch(text))
                return (LocalRe.IsMatch(text) ? "http://" : "https://") + text;   // 내 컴퓨터/내부망 주소는 보통 https 인증서가 없음
            string[] e;
            if (!SearchEngines.TryGetValue(engine, out e)) e = SearchEngines["google"];
            return e[1] + QuotePlus(text);
        }

        // 파이썬 urllib.parse.quote_plus 와 같은 결과 (UTF-8, 공백은 +)
        public static string QuotePlus(string s)
        {
            var sb = new StringBuilder();
            foreach (byte b in Encoding.UTF8.GetBytes(s))
            {
                char c = (char)b;
                if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' || c == '~') sb.Append(c);
                else if (c == ' ') sb.Append('+');
                else sb.Append('%').Append(b.ToString("X2"));
            }
            return sb.ToString();
        }

        // 'v1.10' > '1.9' 처럼 숫자 단위로 비교
        public static bool IsNewer(string candidate, string current)
        {
            Func<string, int[]> t = v => Regex.Matches(v ?? "", "[0-9]+").Cast<Match>().Select(m => int.Parse(m.Value)).ToArray();
            int[] a = t(candidate), b = t(current);
            for (int i = 0; i < Math.Max(a.Length, b.Length); i++)
            {
                int x = i < a.Length ? a[i] : 0, y = i < b.Length ? b[i] : 0;
                if (x != y) return x > y;
            }
            return false;
        }

        // 주소 앞에 보여줄 연결 상태 표시: (아이콘, 설명)
        public static string[] SecurityIcon(string url)
        {
            string lower = (url ?? "").ToLowerInvariant();
            if (lower.StartsWith("https://")) return new[] { "🔒", "암호화된 연결(https)입니다." };
            if (lower.StartsWith("http://")) return new[] { "⚠️", "암호화되지 않은 연결(http)입니다. 비밀번호나 개인정보를 입력하지 마세요." };
            return new[] { "", "" };
        }

        // ---------------------------------------------------------------- 저장 데이터 암호화 (Windows DPAPI)
        // 현재 Windows 사용자 계정으로만 풀 수 있다. 파이썬판(v1.9 이하)과 같은 형식: "YDB1" + DPAPI(JSON).
        static readonly byte[] Magic = Encoding.ASCII.GetBytes("YDB1");

        // JSON을 암호화해 저장한다. 암호화에 성공하면 true, 못 해서 평문으로 저장했으면 false.
        public static bool DumpStore(string path, object obj)
        {
            byte[] raw = new UTF8Encoding(false).GetBytes(Js.Serialize(obj)), payload;
            bool encrypted = true;
            try { payload = Magic.Concat(ProtectedData.Protect(raw, null, DataProtectionScope.CurrentUser)).ToArray(); }
            catch (CryptographicException) { payload = raw; encrypted = false; }
            WriteReplace(path, payload);
            return encrypted;
        }

        // 암호화된 파일과 예전 평문 JSON(v1.5 이하)을 모두 읽는다. 못 읽으면 예외.
        public static Dictionary<string, object> LoadStore(string path)
        {
            byte[] payload = File.ReadAllBytes(path);
            if (payload.Length >= 4 && payload.Take(4).SequenceEqual(Magic))
                payload = ProtectedData.Unprotect(payload.Skip(4).ToArray(), null, DataProtectionScope.CurrentUser);
            string text = Encoding.UTF8.GetString(payload).TrimStart((char)0xFEFF);
            var d = Js.DeserializeObject(text) as Dictionary<string, object>;
            if (d == null) throw new FormatException("저장 파일 형식이 아닙니다");
            return d;
        }

        // ---------------------------------------------------------------- 녹화·원격 프로그램 감지
        public static readonly Dictionary<string, string> CaptureTools = new Dictionary<string, string>
        {
            { "obs64.exe", "OBS Studio" }, { "obs32.exe", "OBS Studio" }, { "bandicam.exe", "Bandicam" }, { "sharex.exe", "ShareX" },
            { "greenshot.exe", "Greenshot" }, { "lightshot.exe", "Lightshot" }, { "gyazo.exe", "Gyazo" }, { "snagit32.exe", "Snagit" },
            { "snagiteditor.exe", "Snagit" }, { "camtasia.exe", "Camtasia" }, { "camrecorder.exe", "Camtasia" },
            { "anydesk.exe", "AnyDesk" }, { "teamviewer.exe", "TeamViewer" }, { "teamviewer_service.exe", "TeamViewer" },
            { "rustdesk.exe", "RustDesk" }, { "parsecd.exe", "Parsec" }, { "vncviewer.exe", "VNC" }, { "tvnserver.exe", "VNC" },
            { "winvnc.exe", "VNC" }, { "remoting_host.exe", "Chrome 원격 데스크톱" },
        };

        [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);

        public static HashSet<string> RunningProcessNames()
        {
            var names = new HashSet<string>();
            foreach (var p in Process.GetProcesses())
                using (p) names.Add((p.ProcessName + ".exe").ToLowerInvariant());
            return names;
        }

        // 실행 중인 화면 녹화·캡처·원격 제어 프로그램 이름 목록 (정렬, 중복 없음)
        public static List<string> FindCaptureTools()
        {
            var found = new SortedSet<string>(StringComparer.Ordinal);
            try
            {
                foreach (var n in RunningProcessNames())
                {
                    string label;
                    if (CaptureTools.TryGetValue(n, out label)) found.Add(label);
                }
                if (GetSystemMetrics(0x1000) != 0) found.Add("원격 데스크톱 세션");   // SM_REMOTESESSION
            }
            catch (Exception) { return new List<string>(); }   // 감지 실패는 감지 안 된 것으로 (파이썬판과 같음)
            return found.ToList();
        }

        // ---------------------------------------------------------------- 로그 파일 관리
        // 로그가 너무 커지면 security.log.1, .2 ... 로 넘기고 오래된 것은 지운다.
        public static void RotateLog(string path, long maxBytes = LogMaxBytes, int keep = LogKeep)
        {
            try
            {
                if (!File.Exists(path) || new FileInfo(path).Length < maxBytes) return;
                string oldest = path + "." + keep;
                if (File.Exists(oldest)) File.Delete(oldest);
                for (int i = keep - 1; i > 0; i--)
                {
                    string src = path + "." + i;
                    if (File.Exists(src)) File.Move(src, path + "." + (i + 1));
                }
                File.Move(path, path + ".1");
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        public static string Now() { return DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"); }

        // 처리되지 않은 오류를 error.log 에 남긴다
        public static void LogError(Exception e)
        {
            try { File.AppendAllText(Path.Combine(App.BaseDir, "error.log"), "[" + Now() + "] v" + App.Version + "\n" + e + "\n\n", Encoding.UTF8); }
            catch (Exception) { }
        }
    }
}
