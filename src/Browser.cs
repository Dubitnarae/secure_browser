// 브라우저 창: 탭, 이동, 북마크·기록, 찾기, 보안(캡처 방지·감지·다운로드 차단·권한 거부), 설정, 업데이트 확인

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace YDBrowser
{
    // 탭 하나 = 웹 화면 하나
    class Tab
    {
        public readonly WebView2 View = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.White };
        public string Title = "새 탭";
        public Image Icon;
        public bool Loading;
        public string Pending;   // 화면 준비 전에 요청된 주소
        public Task Ready;

        public CoreWebView2 Core { get { return View.CoreWebView2; } }

        public string Url
        {
            get
            {
                try { return Core != null ? Core.Source : (Pending ?? ""); }
                catch (Exception) { return Pending ?? ""; }   // 탭이 이미 닫힘
            }
        }
    }

    static class Native
    {
        [DllImport("user32.dll")] public static extern bool SetWindowDisplayAffinity(IntPtr hwnd, uint affinity);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetWindowsHookExW(int id, HookProc proc, IntPtr mod, uint thread);
        [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr h, int n, IntPtr wp, IntPtr lp);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr GetModuleHandleW(string name);
        [DllImport("user32.dll")] public static extern int GetMessageW(out MSG m, IntPtr hwnd, uint min, uint max);
        public delegate IntPtr HookProc(int n, IntPtr wp, IntPtr lp);
        [StructLayout(LayoutKind.Sequential)]
        public struct MSG { public IntPtr hwnd; public uint message; public IntPtr wParam, lParam; public uint time; public int x, y; }

        // 창을 캡처에서 빼거나(0x11, Windows 10 2004 이상), 안 되면 캡처 화면을 검게(0x01). 결과 설명, 실패면 null
        public static string ProtectWindow(IntPtr hwnd)
        {
#if NOPROTECT
            return "시험용 빌드 - 보호 꺼짐";   // 화면 확인용 임시 빌드에서만 (build.ps1 은 이 기호를 쓰지 않음)
#endif
            try
            {
                if (SetWindowDisplayAffinity(hwnd, 0x11)) return "창을 캡처에서 제외";
                if (SetWindowDisplayAffinity(hwnd, 0x01)) return "캡처 화면을 검게 처리";
            }
            catch (Exception) { }
            return null;
        }

        public static bool OurWindowInFront()
        {
            uint pid;
            GetWindowThreadProcessId(GetForegroundWindow(), out pid);
            return pid == (uint)Process.GetCurrentProcess().Id;
        }

        static HookProc keep;   // 대리자가 GC 로 사라지지 않게

        // PrintScreen 감지: 웹 화면(다른 프로세스)에 포커스가 있어도 알 수 있게 키보드 훅을 따로 스레드에서 돌린다
        public static void WatchPrintScreen(Action onPress)
        {
            var th = new Thread(() =>
            {
                keep = (n, wp, lp) =>
                {
                    // PrintScreen 은 누를 때가 아니라 뗄 때만 오는 경우가 있어 둘 다 본다 (0x2C = VK_SNAPSHOT)
                    if (n == 0 && Marshal.ReadInt32(lp) == 0x2C)
                        try { onPress(); } catch (Exception) { }
                    return CallNextHookEx(IntPtr.Zero, n, wp, lp);
                };
                if (SetWindowsHookExW(13, keep, GetModuleHandleW(null), 0) == IntPtr.Zero) return;
                MSG m;
                while (GetMessageW(out m, IntPtr.Zero, 0, 0) > 0) { }
            });
            th.IsBackground = true;
            th.Start();
        }
    }

    // 모든 대화 상자도 캡처 방지를 적용한다 (북마크·방문 기록 목록이 보이므로)
    class Dialog : Form
    {
        public Dialog(string title)
        {
            Text = title;
            Font = Ui.Font;
            BackColor = Color.White;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.CenterParent;
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            Native.ProtectWindow(Handle);
        }
    }

    class MainForm : Form
    {
        readonly string settingsFile = Path.Combine(App.BaseDir, "settings.json");
        readonly string dataFile = Path.Combine(App.BaseDir, "browser_data.json");
        readonly string logFile = Path.Combine(App.BaseDir, "security.log");
        public Dictionary<string, object> Settings;
        List<Dictionary<string, object>> bookmarks = new List<Dictionary<string, object>>();
        List<Dictionary<string, object>> history = new List<Dictionary<string, object>>();
        Dictionary<string, object> session = new Dictionary<string, object>();
        string startupNotice;
        bool plainWarned, inactiveCover, findActive;
        HashSet<string> detected = new HashSet<string>();
        string captureCoverText = "";
        readonly List<string> closedTabs = new List<string>();
        readonly List<Tab> tabs = new List<Tab>();
        Tab current;
        Task<CoreWebView2Environment> env;
        double lastCaptureKey;
        readonly Stopwatch clock = Stopwatch.StartNew();

        // 화면 부품
        readonly Panel page = new Panel { Dock = DockStyle.Fill };
        readonly Panel webHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.White };
        readonly TabStrip strip = new TabStrip { Dock = DockStyle.Top };
        readonly Panel toolbar = new Panel { Dock = DockStyle.Top, BackColor = Color.White };
        readonly FlowLayoutPanel bmBar = new FlowLayoutPanel { Dock = DockStyle.Top, BackColor = Color.White, AutoSize = true, WrapContents = false };
        readonly ProgressBarLine progress = new ProgressBarLine { Dock = DockStyle.Top };
        readonly Panel findBar = new Panel { Dock = DockStyle.Bottom, BackColor = Color.White, Visible = false };
        readonly TextBox findInput = new TextBox { Font = new Font("Malgun Gothic", 10f) };
        readonly Label findCount = new Label { AutoSize = true, ForeColor = Ui.Sub };
        readonly Label status = new Label { Dock = DockStyle.Bottom, Visible = false, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft };
        readonly System.Windows.Forms.Timer statusTimer = new System.Windows.Forms.Timer();
        readonly Label cover = new Label { Dock = DockStyle.Fill, Visible = false, BackColor = Ui.Hex("#202124"), ForeColor = Color.White,
            TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Malgun Gothic", 14f) };
        readonly Panel logPanel = new Panel { Dock = DockStyle.Right, Visible = false, BackColor = Color.White };
        readonly Splitter logSplit = new Splitter { Dock = DockStyle.Right, Visible = false, BackColor = Ui.Line };
        readonly TextBox logView = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill,
            BorderStyle = BorderStyle.None, BackColor = Color.White, ForeColor = Ui.Hex("#2c3e50"), Font = new Font("Consolas", 9f) };
        readonly ListBox suggest = new ListBox { Visible = false, IntegralHeight = false, BorderStyle = BorderStyle.FixedSingle };
        readonly List<string> suggestUrls = new List<string>();
        Omnibox omni;
        IconButton backBtn, fwdBtn, reloadBtn;
        ToolStripMenuItem bmBarItem, logItem;

        public MainForm()
        {
            Text = App.Name + " - 보안 모드";
            Font = Ui.Font;
            BackColor = Color.White;
            Size = new Size(Ui.S(1200), Ui.S(900));
            StartPosition = FormStartPosition.CenterScreen;
            KeyPreview = true;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
            Settings = Core.LoadSettings(settingsFile);
            LoadData();
            strip.Tabs = tabs;

            // 화면: 크롬처럼 [탭 줄] [주소 줄] [북마크바] [진행 표시] [웹 화면] [찾기 줄] [알림 줄]
            page.Controls.Add(webHost);
            page.Controls.Add(progress);
            page.Controls.Add(bmBar);
            page.Controls.Add(BuildToolbar());
            page.Controls.Add(strip);
            page.Controls.Add(BuildFindBar());
            page.Controls.Add(status);
            status.Height = Ui.S(24);
            status.Padding = new Padding(Ui.S(8), 0, 0, 0);
            ResetStatusColor();
            statusTimer.Tick += (s, e) => { statusTimer.Stop(); status.Visible = false; ResetStatusColor(); };
            bmBar.Padding = new Padding(Ui.S(8), Ui.S(1), Ui.S(8), Ui.S(3));

            var logHead = new Label { Text = "Security Logs", Dock = DockStyle.Top, Height = Ui.S(26), TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(Ui.S(6), 0, 0, 0), BackColor = Ui.Hex("#f1f3f4") };
            var logClose = new IconButton("✕", "로그 창 닫기", 22, 8f) { Dock = DockStyle.Right };
            logClose.Click += (s, e) => ToggleLogWindow();
            logHead.Controls.Add(logClose);
            logPanel.Controls.Add(logView);
            logPanel.Controls.Add(logHead);
            logPanel.Width = Ui.S(380);

            Controls.Add(page);
            Controls.Add(logSplit);
            Controls.Add(logPanel);
            Controls.Add(cover);
            Controls.Add(suggest);
            suggest.BringToFront();

            strip.Selected += i => Select(tabs[i]);
            strip.CloseRequested += CloseTab;
            strip.NewRequested += () => NewTab(null);
            strip.MenuRequested += ShowTabMenu;
            strip.Moved += (a, b) =>
            {
                var t = tabs[a];
                tabs.RemoveAt(a);
                tabs.Insert(b, t);
                strip.Current = tabs.IndexOf(current);
                strip.Invalidate();
            };
            suggest.MouseClick += (s, e) => ChooseSuggestion();
            RefreshBookmarkBar();
            RestoreWindowBounds();
        }

        // ---------- 데이터 저장/불러오기 ----------
        static List<Dictionary<string, object>> Items(Dictionary<string, object> d, string key, params string[] need)
        {
            object v;
            var list = new List<Dictionary<string, object>>();
            if (d.TryGetValue(key, out v) && v is object[])
                foreach (var o in (object[])v)
                {
                    var item = o as Dictionary<string, object>;
                    if (item != null && need.All(k => item.ContainsKey(k) && item[k] is string)) list.Add(item);
                }
            return list;
        }

        void LoadData()
        {
            if (!File.Exists(dataFile)) return;
            try
            {
                var d = Core.LoadStore(dataFile);
                bookmarks = Items(d, "bookmarks", "url", "title");
                history = Items(d, "history", "url", "time");
                object s;
                session = d.TryGetValue("session", out s) && s is Dictionary<string, object> ? (Dictionary<string, object>)s : new Dictionary<string, object>();
            }
            catch (Exception)
            {
                // 깨졌거나 풀 수 없는 파일은 덮어쓰지 않고 따로 보관
                string backup = dataFile + ".broken_" + DateTime.Now.ToString("yyyyMMdd_HHmmss");
                try { File.Move(dataFile, backup); } catch (Exception) { }
                startupNotice = "저장 파일을 읽지 못해 " + Path.GetFileName(backup) + " 로 보관하고 새로 시작합니다. (다른 Windows 계정·PC에서 만든 파일이면 열 수 없습니다)";
            }
        }

        void SaveData()
        {
            if (history.Count > Core.MaxHistory) history = history.Skip(history.Count - Core.MaxHistory).ToList();
            var data = new Dictionary<string, object> { { "bookmarks", bookmarks }, { "history", history }, { "session", session } };
            bool encrypted;
            try { encrypted = Core.DumpStore(dataFile, data); }
            catch (IOException e) { AddLog("⚠️ 저장 실패: " + e.Message); return; }
            catch (UnauthorizedAccessException e) { AddLog("⚠️ 저장 실패: " + e.Message); return; }
            if (!encrypted && !plainWarned)
            {
                plainWarned = true;
                AddLog("⚠️ 암호화에 실패해 평문으로 저장했습니다");
            }
        }

        void SaveAppSettings()
        {
            try { Core.SaveSettings(settingsFile, Settings); }
            catch (IOException e) { AddLog("⚠️ 설정 저장 실패: " + e.Message); }
            catch (UnauthorizedAccessException e) { AddLog("⚠️ 설정 저장 실패: " + e.Message); }
        }

        string S(string key) { return (string)Settings[key]; }
        bool B(string key) { return (bool)Settings[key]; }

        // ---------- 화면 구성 ----------
        Control BuildToolbar()
        {
            toolbar.Height = Ui.S(48);
            backBtn = new IconButton("←", "뒤로 (Alt+←)", 32, 13f);
            fwdBtn = new IconButton("→", "앞으로 (Alt+→)", 32, 13f);
            reloadBtn = new IconButton("⟳", "새로고침 (F5)", 32, 13f);
            backBtn.Click += (s, e) => GoBack();
            fwdBtn.Click += (s, e) => GoForward();
            reloadBtn.Click += (s, e) => ReloadOrStop();
            omni = new Omnibox();
            omni.Box.KeyDown += OnUrlKeyDown;
            omni.Box.TextChanged += (s, e) => { if (omni.Box.Focused) RefreshSuggestions(); };
            omni.Box.GotFocus += (s, e) => BeginInvoke((Action)(() => omni.Box.SelectAll()));
            omni.Box.LostFocus += (s, e) => BeginInvoke((Action)(() => { if (!suggest.Focused) suggest.Visible = false; }));
            omni.Star.Click += (s, e) => ToggleBookmark();
            var menuBtn = new IconButton("⋮", "메뉴", 32, 15f);
            var menu = BuildMenu();
            menuBtn.Click += (s, e) => menu.Show(menuBtn, new Point(menuBtn.Width - menu.Width, menuBtn.Height));
            toolbar.Controls.AddRange(new Control[] { backBtn, fwdBtn, reloadBtn, omni, menuBtn });
            toolbar.Layout += (s, e) =>
            {
                int y = (toolbar.Height - backBtn.Height) / 2, x = Ui.S(8);
                foreach (var b in new[] { backBtn, fwdBtn, reloadBtn }) { b.Location = new Point(x, y); x += b.Width + Ui.S(4); }
                menuBtn.Location = new Point(toolbar.Width - menuBtn.Width - Ui.S(8), y);
                omni.SetBounds(x + Ui.S(4), (toolbar.Height - omni.Height) / 2, menuBtn.Left - x - Ui.S(10), omni.Height);
            };
            return toolbar;
        }

        ContextMenuStrip BuildMenu()
        {
            var m = new ContextMenuStrip { Font = Ui.Font, ShowCheckMargin = true, ShowImageMargin = false };
            Func<string, string, Action, ToolStripMenuItem> add = (text, keys, act) =>
            {
                var item = new ToolStripMenuItem(text) { ShortcutKeyDisplayString = keys };
                item.Click += (s, e) => act();
                m.Items.Add(item);
                return item;
            };
            add("새 탭", "Ctrl+T", () => NewTab(null));
            m.Items.Add(new ToolStripSeparator());
            add("북마크 목록", "", ShowBookmarks);
            bmBarItem = add("북마크바 표시", "Ctrl+Shift+B", ToggleBookmarkBar);
            bmBarItem.Checked = B("show_bookmark_bar");
            add("방문 기록", "Ctrl+H", ShowHistory);
            add("닫은 탭 다시 열기", "Ctrl+Shift+T", ReopenClosedTab);
            add("인터넷 사용 기록 삭제…", "Ctrl+Shift+Del", ClearBrowsingData);
            m.Items.Add(new ToolStripSeparator());
            add("페이지에서 찾기", "Ctrl+F", ShowFindBar);
            add("확대", "Ctrl++", () => Zoom(0.1));
            add("축소", "Ctrl+-", () => Zoom(-0.1));
            add("원래 크기", "Ctrl+0", () => Zoom(0));
            add("전체 화면", "F11", ToggleFullscreen);
            m.Items.Add(new ToolStripSeparator());
            logItem = add("보안 로그", "", ToggleLogWindow);
            add("설정", "", OpenSettings);
            return m;
        }

        Control BuildFindBar()
        {
            findBar.Height = Ui.S(44);
            findBar.Paint += (s, e) => { using (var p = new Pen(Ui.Line)) e.Graphics.DrawLine(p, 0, 0, findBar.Width, 0); };
            findInput.TextChanged += (s, e) => FindText(findInput.Text, false, true);
            findInput.KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.Enter) { FindText(findInput.Text, e.Shift, false); e.SuppressKeyPress = true; }
            };
            var up = new IconButton("▲", "이전 찾기 (Shift+F3)", 30, 9f);
            var down = new IconButton("▼", "다음 찾기 (F3)", 30, 9f);
            var close = new IconButton("✕", "닫기 (Esc)", 30, 9f);
            up.Click += (s, e) => FindText(findInput.Text, true, false);
            down.Click += (s, e) => FindText(findInput.Text, false, false);
            close.Click += (s, e) => HideFindBar();
            findBar.Controls.AddRange(new Control[] { findInput, findCount, up, down, close });
            findBar.Layout += (s, e) =>
            {
                int y = (findBar.Height - up.Height) / 2;
                close.Location = new Point(findBar.Width - close.Width - Ui.S(10), y);
                down.Location = new Point(close.Left - down.Width - Ui.S(4), y);
                up.Location = new Point(down.Left - up.Width - Ui.S(4), y);
                findCount.Location = new Point(up.Left - findCount.Width - Ui.S(10), (findBar.Height - findCount.Height) / 2);
                findInput.SetBounds(Ui.S(10), (findBar.Height - findInput.Height) / 2, Math.Max(50, findCount.Left - Ui.S(20)), findInput.Height);
            };
            return findBar;
        }

        void RefreshBookmarkBar()
        {
            bmBar.SuspendLayout();
            foreach (Control c in bmBar.Controls.Cast<Control>().ToList()) c.Dispose();
            bmBar.Controls.Clear();
            if (!B("show_bookmark_bar") || bookmarks.Count == 0)
            {
                bmBar.Visible = false;
                bmBar.ResumeLayout();
                return;
            }
            foreach (var bm in bookmarks.Take(Core.BookmarkBarMax))
            {
                var b0 = bm;
                string title = (string)bm["title"];
                var btn = MakeBmButton(title.Length <= 18 ? title : title.Substring(0, 17) + "…", title + "\n" + bm["url"]);
                btn.MouseUp += (s, e) =>
                {
                    if (e.Button == MouseButtons.Left) OpenBookmark(b0, false);
                    else if (e.Button == MouseButtons.Middle) OpenBookmark(b0, true);
                    else if (e.Button == MouseButtons.Right) BookmarkMenu(btn, b0, e.Location);
                };
                bmBar.Controls.Add(btn);
            }
            if (bookmarks.Count > Core.BookmarkBarMax)
            {
                var more = MakeBmButton("»", "북마크 전체 보기");
                more.Click += (s, e) => ShowBookmarks();
                bmBar.Controls.Add(more);
            }
            bmBar.Visible = true;
            bmBar.ResumeLayout();
        }

        IconButton MakeBmButton(string text, string tip)
        {
            var b = new IconButton(text, tip, 26, 9f) { Round = false, Fore = Ui.Hex("#3c4043"), Font = Ui.Font, Margin = new Padding(Ui.S(1)) };
            b.Width = TextRenderer.MeasureText(text, Ui.Font).Width + Ui.S(16);
            return b;
        }

        void OpenBookmark(Dictionary<string, object> bm, bool newTab)
        {
            if (newTab) NewTab((string)bm["url"]);
            else if (current != null) Navigate(current, (string)bm["url"]);
        }

        void BookmarkMenu(Control btn, Dictionary<string, object> bm, Point pos)
        {
            var m = new ContextMenuStrip { Font = Ui.Font };
            m.Items.Add("열기", null, (s, e) => OpenBookmark(bm, false));
            m.Items.Add("새 탭에서 열기", null, (s, e) => OpenBookmark(bm, true));
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add("삭제", null, (s, e) => RemoveBookmarkUrl((string)bm["url"]));
            m.Show(btn, pos);
        }

        void ResetStatusColor()
        {
            status.BackColor = Ui.Hex("#f1f3f4");
            status.ForeColor = Ui.Hex("#3c4043");
        }

        // 크롬처럼 평소에는 숨기고, 알림이 있을 때만 잠깐 보인다
        void ShowMessage(string text, int ms)
        {
            ResetStatusColor();
            status.Text = text;
            status.Visible = true;
            statusTimer.Stop();
            statusTimer.Interval = ms;
            statusTimer.Start();
        }

        void FlashAlert(string text, int ms = 7000)
        {
            ShowMessage(text, ms);
            status.BackColor = Ui.Hex("#d93025");
            status.ForeColor = Color.White;
        }

        // ---------- 주소창 추천 ----------
        // 북마크(제목 - 주소) 먼저, 그다음 최근 방문 기록(중복 제외). 입력한 글자가 들어 있는 것만 최대 8개
        void RefreshSuggestions()
        {
            string q = omni.Box.Text.Trim();
            suggest.Items.Clear();
            suggestUrls.Clear();
            if (q != "")
            {
                var seen = new HashSet<string>();
                var entries = new List<string[]>();
                foreach (var b in Enumerable.Reverse(bookmarks))
                {
                    entries.Add(new[] { b["title"] + " - " + b["url"], (string)b["url"] });
                    seen.Add((string)b["url"]);
                }
                foreach (var h in Enumerable.Reverse(history.Skip(Math.Max(0, history.Count - Core.SuggestHistoryMax))))
                    if (seen.Add((string)h["url"])) entries.Add(new[] { (string)h["url"], (string)h["url"] });
                foreach (var e in entries.Where(e => e[0].IndexOf(q, StringComparison.OrdinalIgnoreCase) >= 0).Take(8))
                {
                    suggest.Items.Add(e[0]);
                    suggestUrls.Add(e[1]);
                }
            }
            if (suggest.Items.Count == 0) { suggest.Visible = false; return; }
            var p = PointToClient(omni.PointToScreen(new Point(Ui.S(10), omni.Height)));
            suggest.SetBounds(p.X, p.Y, omni.Width - Ui.S(20), suggest.ItemHeight * suggest.Items.Count + 4);
            suggest.Visible = true;
            suggest.BringToFront();
        }

        void ChooseSuggestion()
        {
            int i = suggest.SelectedIndex;
            suggest.Visible = false;
            if (i < 0 || i >= suggestUrls.Count) return;
            omni.Box.Text = suggestUrls[i];
            NavigateToUrl();
        }

        void OnUrlKeyDown(object sender, KeyEventArgs e)
        {
            if (suggest.Visible && (e.KeyCode == Keys.Down || e.KeyCode == Keys.Up))
            {
                int n = suggest.Items.Count, i = suggest.SelectedIndex + (e.KeyCode == Keys.Down ? 1 : -1);
                suggest.SelectedIndex = i < 0 ? -1 : Math.Min(i, n - 1);
                e.Handled = e.SuppressKeyPress = true;
            }
            else if (e.KeyCode == Keys.Enter)
            {
                e.Handled = e.SuppressKeyPress = true;
                if (suggest.Visible && suggest.SelectedIndex >= 0) ChooseSuggestion();
                else NavigateToUrl();
            }
            else if (e.KeyCode == Keys.Escape)
            {
                e.Handled = e.SuppressKeyPress = true;
                suggest.Visible = false;
                if (current != null) ShowUrl(current.Url);
            }
        }

        // ---------- 웹 엔진 ----------
        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            AddLog("브라우저 시작 (v" + App.Version + ")");
            if (startupNotice != null) AddLog("⚠️ " + startupNotice);
            string mode = Native.ProtectWindow(Handle);
            if (mode != null) AddLog("화면 캡처 방지 적용됨 (" + mode + ")");
            else
            {
                AddLog("⚠️ 화면 캡처 방지 적용 실패 - 보호되지 않은 상태입니다");
                MessageBox.Show(this, "화면 캡처 방지를 적용하지 못했습니다.\n이 상태에서는 화면이 캡처될 수 있습니다.", "보안 경고",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            Native.WatchPrintScreen(() =>
            {
                if (Native.OurWindowInFront()) BeginInvoke((Action)OnCaptureKey);
            });
            var timer = new System.Windows.Forms.Timer { Interval = Core.CaptureCheckMs };
            timer.Tick += (s, ev) => CheckCaptureTools();
            timer.Start();
            CheckCaptureTools();
            if (B("check_updates") && S("last_update_check") != DateTime.Now.ToString("yyyy-MM-dd"))
            {
                var once = new System.Windows.Forms.Timer { Interval = 3000 };
                once.Tick += (s, ev) => { once.Dispose(); CheckForUpdates(false); };
                once.Start();
            }
            try
            {
                var options = new CoreWebView2EnvironmentOptions { Language = "ko" };
                env = CoreWebView2Environment.CreateAsync(null, Path.Combine(App.BaseDir, "WebView2Data"), options);
                await env;
            }
            catch (Exception ex)
            {
                Core.LogError(ex);
                MessageBox.Show(this, "웹 화면 엔진(Microsoft Edge WebView2 런타임)을 시작하지 못했습니다.\n\n" +
                    "Windows 10/11 에는 보통 들어 있습니다. 없으면 Microsoft 에서 'WebView2 런타임'을 설치하세요.\n\n" + ex.Message,
                    App.Name, MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            OpenStartTabs();
        }

        async Task InitTab(Tab t)
        {
            var e = await env;
            await t.View.EnsureCoreWebView2Async(e);
            var c = t.Core;
            var st = c.Settings;
            st.AreDevToolsEnabled = false;                // 개발자 도구(F12) 막기
            st.AreBrowserAcceleratorKeysEnabled = false;  // 엔진 자체 단축키(저장·인쇄·소스 보기 등) 끄기 — 필요한 단축키는 이 창이 처리
            st.IsPasswordAutosaveEnabled = false;
            st.IsGeneralAutofillEnabled = false;
            st.IsStatusBarEnabled = true;
            await c.AddScriptToExecuteOnDocumentCreatedAsync("window.print = function () {};");   // 인쇄 막기
            c.SourceChanged += (s, a) => OnUrlChanged(t);
            c.DocumentTitleChanged += (s, a) => OnTitleChanged(t);
            c.HistoryChanged += (s, a) => { if (t == current) UpdateNavButtons(); };
            c.NavigationStarting += (s, a) => OnLoadProgress(t, 15);
            c.ContentLoading += (s, a) => OnLoadProgress(t, 60);
            c.NavigationCompleted += (s, a) => OnLoadProgress(t, 100);
            c.FaviconChanged += async (s, a) =>
            {
                try
                {
                    using (var stream = await c.GetFaviconAsync(CoreWebView2FaviconImageFormat.Png))
                    using (var ms = new MemoryStream())   // 엔진이 주는 흐름은 길이·되감기가 안 될 수 있어 먼저 복사
                    {
                        if (stream != null) stream.CopyTo(ms);
                        if (ms.Length == 0) t.Icon = null;
                        else { ms.Position = 0; using (var img = Image.FromStream(ms)) t.Icon = new Bitmap(img); }
                    }
                }
                catch (Exception) { t.Icon = null; }
                strip.Invalidate();
            };
            c.NewWindowRequested += OnNewWindow;
            c.DownloadStarting += OnDownloadStarting;
            c.PermissionRequested += (s, a) =>
            {
                // 카메라·마이크·위치 등 권한 요청은 모두 거부
                a.State = CoreWebView2PermissionState.Deny;
                string host;
                try { host = new Uri(a.Uri).Host; } catch (UriFormatException) { host = a.Uri; }
                AddLog("⚠️ 권한 요청 거부: " + a.PermissionKind + " (" + host + ")");
            };
            c.ContextMenuRequested += (s, a) =>
            {
                // 저장·인쇄·캡처·공유·검사 메뉴는 보안 브라우저라서 뺀다
                var drop = new HashSet<string> { "saveAs", "print", "createQrCode", "inspectElement", "webCapture", "share",
                    "saveImageAs", "saveLinkAs", "saveMediaAs", "viewPageSource" };
                foreach (var item in a.MenuItems.ToList()) if (drop.Contains(item.Name)) a.MenuItems.Remove(item);
            };
            c.Find.MatchCountChanged += (s, a) => { if (t == current) UpdateFindCount(); };
            c.Find.ActiveMatchIndexChanged += (s, a) => { if (t == current) UpdateFindCount(); };
            c.ContainsFullScreenElementChanged += (s, a) => SetFullscreen(c.ContainsFullScreenElement);   // 동영상 전체 화면
            if (t.Pending != null) Go(t, t.Pending);
        }

        void Go(Tab t, string url)
        {
            try { t.Core.Navigate(url); }
            catch (ArgumentException) { ShowMessage("열 수 없는 주소입니다: " + url, 4000); }
        }

        void Navigate(Tab t, string url)
        {
            if (t.Core != null) Go(t, url);
            else t.Pending = url;
        }

        // ---------- 탭 ----------
        Tab NewTab(string url, bool switchTo = true, bool load = true, int index = -1)
        {
            if (tabs.Count >= Core.MaxTabs)
            {
                ShowMessage("탭은 최대 " + Core.MaxTabs + "개까지 열 수 있습니다.", 3000);
                return null;
            }
            var t = new Tab();
            if (load) t.Pending = url ?? S("home_url");
            tabs.Insert(index < 0 || index > tabs.Count ? tabs.Count : index, t);
            webHost.Controls.Add(t.View);
            t.View.Visible = false;
            t.Ready = InitTab(t);
            t.Ready.ContinueWith(r => { if (r.Exception != null) Core.LogError(r.Exception); }, TaskScheduler.FromCurrentSynchronizationContext());
            if (switchTo || current == null) Select(t);
            else strip.Invalidate();
            return t;
        }

        // 페이지가 새 창을 요구할 때 (target=_blank, window.open) 현재 탭 바로 오른쪽에 새 탭으로 연다
        async void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            var deferral = e.GetDeferral();
            try
            {
                var t = NewTab(null, true, false, tabs.IndexOf(current) + 1);
                if (t == null) { e.Handled = true; return; }
                await t.Ready;
                e.NewWindow = t.Core;
            }
            catch (Exception ex) { Core.LogError(ex); e.Handled = true; }
            finally { deferral.Complete(); }
        }

        void Select(Tab t)
        {
            if (t == null) return;
            var old = current;
            current = t;
            t.View.Visible = true;
            t.View.BringToFront();
            if (old != null && old != t) old.View.Visible = false;
            strip.Current = tabs.IndexOf(t);
            strip.Invalidate();
            ShowUrl(t.Url);
            OnTitleChanged(t);
            progress.Value = 0;
            UpdateReloadButton();
            UpdateNavButtons();
            if (findBar.Visible) FindText(findInput.Text, false, true);
            if (!omni.Box.Focused && t.Url != "") t.View.Focus();
        }

        void CloseTab(int index)
        {
            if (index < 0 || index >= tabs.Count) return;
            if (tabs.Count == 1)
            {
                Navigate(tabs[0], S("home_url"));
                return;
            }
            CloseView(tabs[index]);
        }

        // 탭을 닫고, 나중에 다시 열 수 있게 주소를 기억한다
        void CloseView(Tab t)
        {
            int i = tabs.IndexOf(t);
            if (i < 0) return;
            string url = t.Url;
            if (url != "" && url != "about:blank")
            {
                closedTabs.Add(url);
                if (closedTabs.Count > Core.ClosedTabsMax) closedTabs.RemoveAt(0);
            }
            tabs.RemoveAt(i);
            if (t == current) Select(tabs[Math.Min(i, tabs.Count - 1)]);
            webHost.Controls.Remove(t.View);
            t.View.Dispose();
            strip.Current = tabs.IndexOf(current);
            strip.Invalidate();
        }

        void ReopenClosedTab()
        {
            if (closedTabs.Count == 0) { ShowMessage("다시 열 수 있는 탭이 없습니다.", 2500); return; }
            string url = closedTabs[closedTabs.Count - 1];
            closedTabs.RemoveAt(closedTabs.Count - 1);
            NewTab(url);
        }

        // 탭 우클릭 메뉴 (구성과 순서는 Chromium 의 탭 메뉴를 따름)
        void ShowTabMenu(int index, Point screen)
        {
            var t = tabs[index];
            var m = new ContextMenuStrip { Font = Ui.Font };
            m.Items.Add("오른쪽에 새 탭", null, (s, e) => NewTab(null, true, true, index + 1));
            m.Items.Add("URL 복사", null, (s, e) => { if (t.Url != "") Clipboard.SetText(t.Url); });
            m.Items.Add("새로고침", null, (s, e) => { if (t.Core != null) t.Core.Reload(); });
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add("탭 닫기", null, (s, e) => CloseTab(tabs.IndexOf(t)));
            m.Items.Add("다른 탭 닫기", null, (s, e) => { foreach (var x in tabs.Where(x => x != t).ToList()) CloseView(x); }).Enabled = tabs.Count > 1;
            m.Items.Add("오른쪽 탭 닫기", null, (s, e) => { foreach (var x in tabs.Skip(tabs.IndexOf(t) + 1).ToList()) CloseView(x); }).Enabled = index < tabs.Count - 1;
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add("닫은 탭 다시 열기", null, (s, e) => ReopenClosedTab()).Enabled = closedTabs.Count > 0;
            m.Show(screen);
        }

        void SwitchTab(int step)
        {
            if (tabs.Count > 0) Select(tabs[((tabs.IndexOf(current) + step) % tabs.Count + tabs.Count) % tabs.Count]);
        }

        // Ctrl+1~8: 그 번호의 탭, Ctrl+9: 마지막 탭 (크롬과 같음)
        void SelectTabNumber(int n)
        {
            if (tabs.Count > 0) Select(tabs[n == 9 ? tabs.Count - 1 : Math.Min(n - 1, tabs.Count - 1)]);
        }

        void MoveCurrent(int step)
        {
            int i = tabs.IndexOf(current), j = i + step;
            if (i < 0 || j < 0 || j >= tabs.Count) return;
            tabs.RemoveAt(i);
            tabs.Insert(j, current);
            strip.Current = j;
            strip.Invalidate();
        }

        bool fullscreen;
        FormWindowState beforeFull;

        void ToggleFullscreen() { SetFullscreen(!fullscreen); }

        void SetFullscreen(bool on)
        {
            if (on == fullscreen) return;
            fullscreen = on;
            strip.Visible = toolbar.Visible = !on;
            bmBar.Visible = !on && B("show_bookmark_bar") && bookmarks.Count > 0;
            if (on)
            {
                beforeFull = WindowState;
                FormBorderStyle = FormBorderStyle.None;
                WindowState = FormWindowState.Normal;   // 최대화 상태에서 바로 바꾸면 작업 표시줄이 남는다
                WindowState = FormWindowState.Maximized;
            }
            else
            {
                FormBorderStyle = FormBorderStyle.Sizable;
                WindowState = beforeFull;
            }
        }

        void GoHome() { if (current != null) Navigate(current, S("home_url")); }

        // Ctrl+Shift+R, Ctrl+F5: 캐시를 무시하고 새로고침
        async void HardReload()
        {
            if (current == null || current.Core == null) return;
            try { await current.Core.CallDevToolsProtocolMethodAsync("Page.reload", "{\"ignoreCache\":true}"); }
            catch (Exception) { current.Core.Reload(); }
        }

        // Ctrl+Shift+Delete: 방문 기록, 캐시, 쿠키를 모두 삭제
        async void ClearBrowsingData()
        {
            if (MessageBox.Show(this, "방문 기록, 캐시, 쿠키(로그인 정보)를 모두 삭제할까요?\n북마크와 설정은 유지됩니다.", "인터넷 사용 기록 삭제",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
            history.Clear();
            closedTabs.Clear();
            SaveData();
            var t = tabs.FirstOrDefault(x => x.Core != null);
            if (t != null)
                try { await t.Core.Profile.ClearBrowsingDataAsync(); }
                catch (Exception ex) { AddLog("⚠️ 캐시·쿠키 삭제 실패: " + ex.Message); }
            AddLog("인터넷 사용 기록 삭제 (방문 기록, 캐시, 쿠키)");
            ShowMessage("인터넷 사용 기록을 삭제했습니다.", 3000);
        }

        void OpenStartTabs()
        {
            var urls = new List<string>();
            object o;
            if (S("start_mode") == "last" && session.TryGetValue("tabs", out o) && o is object[])
                urls = ((object[])o).OfType<string>().Where(u => u != "").Take(Core.MaxTabs).ToList();
            if (urls.Count == 0) urls.Add(S("home_url"));
            foreach (var u in urls) NewTab(u, false);
            Select(tabs[0]);
        }

        // ---------- 페이지 이벤트 ----------
        void OnUrlChanged(Tab t)
        {
            string url = t.Url;
            if (t == current)
            {
                if (!omni.Box.Focused) ShowUrl(url);
                else UpdateStar(url);
                UpdateNavButtons();
            }
            if (url == "" || url == "about:blank" || !B("save_history")) return;
            if (history.Count == 0 || (string)history[history.Count - 1]["url"] != url)
            {
                history.Add(new Dictionary<string, object> { { "url", url }, { "time", Core.Now() } });
                SaveData();
            }
        }

        void ShowUrl(string url)
        {
            omni.Box.Text = url == "about:blank" ? "" : url;
            var icon = Core.SecurityIcon(url);
            omni.SetSecurity(icon[0], icon[1]);
            UpdateStar(url);
        }

        bool IsBookmarked(string url) { return bookmarks.Any(b => (string)b["url"] == url); }

        void UpdateStar(string url)
        {
            bool marked = IsBookmarked(url);
            omni.Star.Text = marked ? "★" : "☆";
            omni.Star.Fore = marked ? Ui.Blue : Ui.Sub;
            omni.Star.Tip = marked ? "북마크 삭제 (Ctrl+D)" : "이 페이지를 북마크 (Ctrl+D)";
            omni.Star.Invalidate();
        }

        void UpdateNavButtons()
        {
            var c = current != null ? current.Core : null;
            backBtn.Enabled = c != null && c.CanGoBack;
            fwdBtn.Enabled = c != null && c.CanGoForward;
        }

        void UpdateReloadButton()
        {
            bool loading = current != null && current.Loading;
            reloadBtn.Text = loading ? "✕" : "⟳";
            reloadBtn.Tip = loading ? "로드 중지" : "새로고침 (F5)";
        }

        void OnTitleChanged(Tab t)
        {
            string title = "";
            try { if (t.Core != null) title = t.Core.DocumentTitle; } catch (Exception) { }
            t.Title = title != "" ? title : "새 탭";   // 길면 탭 줄이 알아서 줄여서 보여준다
            strip.Invalidate();
            if (t == current) Text = title != "" ? title + " - " + App.Name : App.Name + " - 보안 모드";
        }

        void OnLoadProgress(Tab t, int percent)
        {
            t.Loading = percent < 100;
            if (t != current) return;
            progress.Value = percent < 100 ? percent : 0;
            UpdateReloadButton();
            if (percent >= 100) UpdateNavButtons();
        }

        void OnDownloadStarting(object sender, CoreWebView2DownloadStartingEventArgs e)
        {
            string name = Path.GetFileName(e.ResultFilePath);
            if (B("allow_downloads"))
            {
                AddLog("다운로드 허용: " + name);
                return;
            }
            e.Cancel = true;
            AddLog("⚠️ 다운로드 차단: " + name);
            ShowMessage("🚫 다운로드가 차단되었습니다: " + name, 5000);
        }

        // ---------- 이동 ----------
        void GoBack() { if (current != null && current.Core != null && current.Core.CanGoBack) current.Core.GoBack(); }
        void GoForward() { if (current != null && current.Core != null && current.Core.CanGoForward) current.Core.GoForward(); }
        void ReloadPage() { if (current != null && current.Core != null) current.Core.Reload(); }

        // 불러오는 중이면 중지, 아니면 새로고침 (크롬의 ⟳ / ✕ 버튼)
        void ReloadOrStop()
        {
            if (current == null || current.Core == null) return;
            if (current.Loading) current.Core.Stop();
            else current.Core.Reload();
        }

        void FocusUrlBar()
        {
            omni.Box.Focus();
            omni.Box.SelectAll();
        }

        // 핵심: 주소면 이동, 아니면 검색
        void NavigateToUrl()
        {
            string text = omni.Box.Text.Trim();
            suggest.Visible = false;
            if (text == "" || current == null) return;
            Navigate(current, Core.ResolveInput(text, S("search_engine")));
            current.View.Focus();
        }

        // ---------- 찾기 / 확대·축소 ----------
        void ShowFindBar()
        {
            findBar.Visible = true;
            findInput.Focus();
            findInput.SelectAll();
            if (findInput.Text != "") FindText(findInput.Text, false, true);
        }

        void HideFindBar()
        {
            findBar.Visible = false;
            findCount.Text = "";
            if (current != null && current.Core != null && findActive) current.Core.Find.Stop();   // 강조 표시 지우기
            findActive = false;
            if (current != null) current.View.Focus();
        }

        async void FindText(string text, bool backward, bool restart)
        {
            if (current == null || current.Core == null) return;
            var c = current.Core;
            try
            {
                if (text == "")
                {
                    if (findActive) c.Find.Stop();
                    findActive = false;
                    findCount.Text = "";
                    return;
                }
                if (restart || !findActive)
                {
                    var o = (await env).CreateFindOptions();
                    o.FindTerm = text;
                    o.SuppressDefaultFindDialog = true;
                    o.ShouldHighlightAllMatches = true;
                    findActive = true;
                    await c.Find.StartAsync(o);
                    if (backward) c.Find.FindPrevious();
                }
                else if (backward) c.Find.FindPrevious();
                else c.Find.FindNext();
                UpdateFindCount();
            }
            catch (Exception ex) { Core.LogError(ex); }
        }

        void UpdateFindCount()
        {
            if (!findActive || current == null || current.Core == null) return;
            int n = current.Core.Find.MatchCount, i = current.Core.Find.ActiveMatchIndex;
            findCount.Text = n > 0 ? (i + 1) + "/" + n : "없음";
            findBar.PerformLayout();
        }

        // F3, Ctrl+G: 다음 찾기 / Shift+F3, Ctrl+Shift+G: 이전 찾기
        void FindAgain(bool backward)
        {
            if (!findBar.Visible) ShowFindBar();
            else if (findInput.Text != "") FindText(findInput.Text, backward, false);
        }

        void Zoom(double delta)
        {
            if (current == null || current.Core == null) return;
            double f = delta == 0 ? 1.0 : Math.Min(5.0, Math.Max(0.25, Math.Round(current.View.ZoomFactor + delta, 2)));
            current.View.ZoomFactor = f;
            ShowMessage("화면 크기 " + (int)Math.Round(f * 100) + "%", 2000);
        }

        // ---------- 북마크 / 기록 ----------
        void AddBookmark()
        {
            if (current == null) return;
            string url = current.Url;
            if (url == "" || url == "about:blank") return;
            if (IsBookmarked(url)) { ShowMessage("이미 북마크에 있는 주소입니다.", 3000); return; }
            string title = AskText("북마크 추가", "이름:", current.Title == "새 탭" ? url : current.Title);
            if (string.IsNullOrEmpty(title)) return;
            bookmarks.Add(new Dictionary<string, object> { { "title", title }, { "url", url } });
            SaveData();
            BookmarksChanged();
            ShowMessage("북마크에 추가했습니다.", 3000);
        }

        // 주소창의 별(☆/★), Ctrl+D: 없으면 추가, 이미 있으면 삭제
        void ToggleBookmark()
        {
            if (current == null) return;
            if (IsBookmarked(current.Url)) RemoveBookmarkUrl(current.Url);
            else AddBookmark();
        }

        void RemoveBookmarkUrl(string url)
        {
            bookmarks = bookmarks.Where(b => (string)b["url"] != url).ToList();
            SaveData();
            BookmarksChanged();
            ShowMessage("북마크를 삭제했습니다.", 3000);
        }

        void BookmarksChanged()
        {
            RefreshBookmarkBar();
            if (current != null) UpdateStar(current.Url);
        }

        void ToggleBookmarkBar()
        {
            Settings["show_bookmark_bar"] = !B("show_bookmark_bar");
            bmBarItem.Checked = B("show_bookmark_bar");
            SaveAppSettings();
            RefreshBookmarkBar();
        }

        void ShowBookmarks() { ShowListDialog("북마크 목록", false); }
        void ShowHistory() { ShowListDialog("방문 기록", true); }

        void ShowListDialog(string title, bool isHistory)
        {
            using (var d = new Dialog(title) { FormBorderStyle = FormBorderStyle.Sizable, MinimizeBox = false, Size = new Size(Ui.S(520), Ui.S(560)) })
            {
                var list = new ListBox { Dock = DockStyle.Fill, IntegralHeight = false, BorderStyle = BorderStyle.FixedSingle, HorizontalScrollbar = true };
                var rows = new List<int>();   // 목록 줄 → 원래 번호 (주소는 표시 글자에서 잘라내지 않음)
                Action fill = () =>
                {
                    var src = isHistory ? history : bookmarks;
                    list.BeginUpdate();
                    list.Items.Clear();
                    rows.Clear();
                    for (int i = src.Count - 1; i >= 0; i--)
                    {
                        var it = src[i];
                        list.Items.Add(isHistory ? it["time"] + "  " + it["url"] : it["title"] + " (" + it["url"] + ")");
                        rows.Add(i);
                    }
                    list.EndUpdate();
                };
                fill();
                list.DoubleClick += (s, e) =>
                {
                    if (list.SelectedIndex < 0 || current == null) return;
                    var src = isHistory ? history : bookmarks;
                    Navigate(current, (string)src[rows[list.SelectedIndex]]["url"]);
                    d.Close();
                };
                var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(Ui.S(6)) };
                var close = new Button { Text = "닫기", AutoSize = true };
                close.Click += (s, e) => d.Close();
                bottom.Controls.Add(close);
                if (isHistory)
                {
                    var clear = new Button { Text = "기록 전체 삭제", AutoSize = true };
                    clear.Click += (s, e) =>
                    {
                        if (MessageBox.Show(d, "방문 기록을 모두 삭제할까요?", "기록 삭제", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
                        history.Clear();
                        SaveData();
                        fill();
                        AddLog("방문 기록 삭제");
                    };
                    bottom.Controls.Add(clear);
                }
                else
                {
                    var del = new Button { Text = "선택 삭제", AutoSize = true };
                    del.Click += (s, e) =>
                    {
                        if (list.SelectedIndex < 0) return;
                        bookmarks.RemoveAt(rows[list.SelectedIndex]);
                        SaveData();
                        BookmarksChanged();
                        fill();   // 번호가 밀리므로 다시 채움
                    };
                    bottom.Controls.Add(del);
                }
                d.Controls.Add(list);
                d.Controls.Add(bottom);
                d.Padding = new Padding(Ui.S(8));
                d.ShowDialog(this);
            }
        }

        string AskText(string title, string label, string value)
        {
            using (var d = new Dialog(title) { AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Padding = new Padding(Ui.S(10)) })
            {
                var box = new TextBox { Text = value, Width = Ui.S(320) };
                var ok = new Button { Text = "확인", DialogResult = DialogResult.OK, AutoSize = true };
                var cancel = new Button { Text = "취소", DialogResult = DialogResult.Cancel, AutoSize = true };
                var g = new TableLayoutPanel { AutoSize = true, ColumnCount = 2, Dock = DockStyle.Fill };
                g.Controls.Add(new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Left });
                g.Controls.Add(box);
                var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill };
                buttons.Controls.Add(cancel);
                buttons.Controls.Add(ok);
                g.Controls.Add(buttons);
                g.SetColumnSpan(buttons, 2);
                d.Controls.Add(g);
                d.AcceptButton = ok;
                d.CancelButton = cancel;
                return d.ShowDialog(this) == DialogResult.OK ? box.Text.Trim() : null;
            }
        }

        // ---------- 설정 ----------
        void OpenSettings()
        {
            using (var d = new SettingsDialog(this))
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                foreach (var kv in d.Values()) Settings[kv.Key] = kv.Value;
                SaveAppSettings();
                AddLog("설정 변경됨");
                CheckCaptureTools();
                inactiveCover = B("blur_on_inactive") && !Native.OurWindowInFront();
                UpdateCover();
            }
        }

        public void OpenDataFolder() { Process.Start("explorer.exe", "\"" + App.BaseDir + "\""); }

        // ---------- 로그 / 보안 ----------
        void ToggleLogWindow()
        {
            logPanel.Visible = logSplit.Visible = !logPanel.Visible;
            logItem.Checked = logPanel.Visible;
        }

        void AddLog(string message)
        {
            string line = "[" + Core.Now() + "] " + message;
            logView.AppendText((logView.TextLength > 0 ? "\r\n" : "") + line);
            // 프로그램을 꺼도 남도록 파일에도 기록 (너무 커지면 새 파일로 교체)
            Core.RotateLog(logFile);
            try { File.AppendAllText(logFile, line + "\n", new UTF8Encoding(false)); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        void OnCaptureKey()
        {
            double now = clock.Elapsed.TotalSeconds;
            if (now - lastCaptureKey < 0.5) return;   // 누름·뗌 두 번 오는 것을 한 번으로
            lastCaptureKey = now;
            AddLog("⚠️ 캡처 시도 감지 (PrintScreen)");
            if (!logPanel.Visible) ToggleLogWindow();
            FlashAlert("🚨 보안 경고: 캡처 동작 감지!");
            // 클립보드에 복사된 내용 제거 (캡처 직후 들어오므로 잠시 뒤에 비움)
            var t = new System.Windows.Forms.Timer { Interval = 300 };
            t.Tick += (s, e) =>
            {
                t.Dispose();
                try { Clipboard.Clear(); } catch (ExternalException) { }
            };
            t.Start();
        }

        void CheckCaptureTools()
        {
            string action = S("capture_tool_action");
            var found = action != "off" ? Core.FindCaptureTools() : new List<string>();
            var neu = found.Where(f => !detected.Contains(f)).OrderBy(f => f, StringComparer.Ordinal).ToList();
            var gone = detected.Where(f => !found.Contains(f)).OrderBy(f => f, StringComparer.Ordinal).ToList();
            if (neu.Count > 0)
            {
                string names = string.Join(", ", neu);
                AddLog("⚠️ 녹화·원격 프로그램 감지: " + names);
                if (!logPanel.Visible) ToggleLogWindow();
                FlashAlert("🚨 녹화·원격 프로그램이 실행 중입니다: " + names, 10000);
            }
            if (gone.Count > 0) AddLog("녹화·원격 프로그램 종료 확인: " + string.Join(", ", gone));
            detected = new HashSet<string>(found);
            captureCoverText = action == "block" && found.Count > 0
                ? "🔒 화면 녹화·원격 프로그램이 감지되어 화면을 가렸습니다.\n\n" + string.Join(", ", found) + "\n\n해당 프로그램을 종료하면 다시 표시됩니다."
                : "";
            UpdateCover();
        }

        protected override void WndProc(ref Message m)
        {
            base.WndProc(ref m);
            if (m.Msg == 0x1C)   // WM_ACTIVATEAPP: 이 프로그램의 창 전체가 활성/비활성으로 바뀜
            {
                inactiveCover = Settings != null && B("blur_on_inactive") && m.WParam == IntPtr.Zero;
                UpdateCover();
            }
        }

        // 가림막: 녹화 프로그램 감지가 우선, 그다음 다른 창으로 전환했을 때
        void UpdateCover()
        {
            string text = captureCoverText;
            if (text == "" && inactiveCover) text = "🔒 다른 창으로 전환되어 화면을 가렸습니다.\n\n이 창을 클릭하면 다시 표시됩니다.";
            bool on = text != "";
            if (on) cover.Text = text;
            if (cover.Visible == on) return;
            page.Visible = !on;   // 웹 화면을 숨겨야 가림막 뒤로 비치지 않는다
            cover.Visible = on;
            if (on) cover.BringToFront();
        }

        // ---------- 업데이트 확인 (알림만, 다운로드는 기본 브라우저로) ----------
        public void CheckForUpdates(bool manual)
        {
            Task.Run(() =>
            {
                ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;   // TLS 1.2
                using (var wc = new WebClient { Encoding = Encoding.UTF8 })
                {
                    wc.Headers[HttpRequestHeader.UserAgent] = "YD-Browser";
                    wc.Headers[HttpRequestHeader.Accept] = "application/vnd.github+json";
                    return wc.DownloadString("https://api.github.com/repos/" + App.Repo + "/releases/latest");
                }
            }).ContinueWith(r => OnUpdateReply(r, manual), TaskScheduler.FromCurrentSynchronizationContext());
        }

        void OnUpdateReply(Task<string> r, bool manual)
        {
            string tag, page, body;
            try
            {
                if (r.IsFaulted) throw r.Exception.InnerException;
                var info = (Dictionary<string, object>)Core.Js.DeserializeObject(r.Result);
                Func<string, string> get = k => { object v; return info.TryGetValue(k, out v) && v != null ? v.ToString() : ""; };
                tag = get("tag_name"); page = get("html_url"); body = get("body").Trim();
            }
            catch (Exception)
            {
                if (manual) MessageBox.Show(this, "새 버전 정보를 가져오지 못했습니다.\n인터넷 연결을 확인하거나, 아직 릴리스가 없을 수 있습니다.", "업데이트 확인");
                return;
            }
            Settings["last_update_check"] = DateTime.Now.ToString("yyyy-MM-dd");
            SaveAppSettings();
            if (Core.IsNewer(tag, App.Version))
            {
                if (body.Length > 800) body = body.Substring(0, 800) + "…";
                var answer = MessageBox.Show(this, "새 버전 " + tag + " 이(가) 나왔습니다. (현재 v" + App.Version + ")\n\n" + body +
                    "\n\n다운로드 페이지를 열까요?", "새 버전이 있습니다", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                if (answer == DialogResult.Yes && page.StartsWith("https://github.com/"))
                    Process.Start(new ProcessStartInfo(page) { UseShellExecute = true });   // 다운로드는 이 브라우저가 아닌 기본 브라우저로
            }
            else if (manual) MessageBox.Show(this, "최신 버전입니다. (v" + App.Version + ")", "업데이트 확인");
        }

        // ---------- 창 상태 ----------
        void RestoreWindowBounds()
        {
            object o;
            if (!session.TryGetValue("bounds", out o) || !(o is object[])) return;
            var a = ((object[])o).Select(x => Convert.ToInt32(x)).ToArray();
            if (a.Length != 5) return;
            var r = new Rectangle(a[0], a[1], a[2], a[3]);
            if (r.Width < 300 || r.Height < 200 || !Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(r))) return;
            StartPosition = FormStartPosition.Manual;
            Bounds = r;
            if (a[4] == 1) WindowState = FormWindowState.Maximized;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (fullscreen) SetFullscreen(false);
            var r = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
            session["bounds"] = new object[] { r.X, r.Y, r.Width, r.Height, WindowState == FormWindowState.Maximized ? 1 : 0 };
            if (B("save_history")) session["tabs"] = tabs.Select(t => t.Url).Where(u => u != "" && u != "about:blank").ToArray();
            else session.Remove("tabs");
            SaveData();
            AddLog("브라우저 종료");
            base.OnFormClosing(e);
        }

        // ---------- 단축키 (Chromium 단축키 표 참고) ----------
        // 저장(Ctrl+S)·인쇄(Ctrl+P)·소스 보기(Ctrl+U)는 보안 브라우저라서 일부러 넣지 않았다.
        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            Action a = Shortcut(keyData);
            if (a != null) { a(); return true; }
            return base.ProcessCmdKey(ref msg, keyData);
        }

        Action Shortcut(Keys k)
        {
            Keys code = k & Keys.KeyCode, mods = k & Keys.Modifiers;
            bool c = mods == Keys.Control, cs = mods == (Keys.Control | Keys.Shift), al = mods == Keys.Alt, none = mods == Keys.None, sh = mods == Keys.Shift;
            if ((c && (code == Keys.L || code == Keys.E || code == Keys.K)) || (none && code == Keys.F6) || (al && code == Keys.D)) return FocusUrlBar;
            if ((none && code == Keys.F5) || (c && code == Keys.R)) return ReloadPage;
            if ((cs && code == Keys.R) || (c && code == Keys.F5) || (sh && code == Keys.F5)) return HardReload;
            if (al && code == Keys.Left) return GoBack;
            if (al && code == Keys.Right) return GoForward;
            if (al && code == Keys.Home) return GoHome;
            if (c && code == Keys.T) return () => NewTab(null);
            if (cs && code == Keys.T) return ReopenClosedTab;
            if (c && (code == Keys.W || code == Keys.F4)) return () => CloseTab(tabs.IndexOf(current));
            if (c && (code == Keys.Tab || code == Keys.PageDown)) return () => SwitchTab(1);
            if ((cs && code == Keys.Tab) || (c && code == Keys.PageUp)) return () => SwitchTab(-1);
            if (cs && code == Keys.PageDown) return () => MoveCurrent(1);
            if (cs && code == Keys.PageUp) return () => MoveCurrent(-1);
            if (c && code >= Keys.D1 && code <= Keys.D9) { int n = code - Keys.D0; return () => SelectTabNumber(n); }
            if (c && code == Keys.F) return ShowFindBar;
            if ((none && code == Keys.F3) || (c && code == Keys.G)) return () => FindAgain(false);
            if ((sh && code == Keys.F3) || (cs && code == Keys.G)) return () => FindAgain(true);
            if (c && code == Keys.D) return ToggleBookmark;
            if (c && code == Keys.H) return ShowHistory;
            if (cs && code == Keys.B) return ToggleBookmarkBar;
            if (cs && code == Keys.Delete) return ClearBrowsingData;
            if (none && code == Keys.F11) return ToggleFullscreen;
            if ((c || cs) && (code == Keys.Oemplus || code == Keys.Add)) return () => Zoom(0.1);
            if (c && (code == Keys.OemMinus || code == Keys.Subtract)) return () => Zoom(-0.1);
            if (c && (code == Keys.D0 || code == Keys.NumPad0)) return () => Zoom(0);
            if (none && code == Keys.Escape && findBar.Visible && !omni.Box.Focused) return HideFindBar;
            if (none && code == Keys.Escape && fullscreen) return () => SetFullscreen(false);
            return null;
        }

        // 자체 시험용: 단축키가 무엇에 연결되는지
        public bool HasShortcut(Keys k) { return Shortcut(k) != null; }
    }

    // 주소 줄 아래 2픽셀 파란 진행 표시
    class ProgressBarLine : Control
    {
        int value;
        public ProgressBarLine()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, true);
            Height = Math.Max(2, Ui.S(2));
            BackColor = Ui.Line;
        }

        public int Value { get { return value; } set { this.value = value; Invalidate(); } }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(value > 0 ? BackColor : Color.White);
            if (value > 0) using (var b = new SolidBrush(Ui.Blue)) e.Graphics.FillRectangle(b, 0, 0, Width * value / 100, Height);
        }
    }

    class SettingsDialog : Dialog
    {
        readonly TextBox home;
        readonly ComboBox engine, startMode, capture;
        readonly CheckBox saveHistory, allowDownloads, blur, checkUpdates;

        static ComboBox Combo(Dictionary<string, string> items, string value)
        {
            var c = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = Ui.S(200) };
            foreach (var kv in items) c.Items.Add(kv.Value);
            c.SelectedIndex = Math.Max(0, items.Keys.ToList().IndexOf(value));
            return c;
        }

        static string Key(Dictionary<string, string> items, ComboBox c) { return items.Keys.ElementAt(c.SelectedIndex); }

        public SettingsDialog(MainForm w) : base("설정")
        {
            var s = w.Settings;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Padding = new Padding(Ui.S(10));
            var g = new TableLayoutPanel { AutoSize = true, ColumnCount = 2 };
            Action<string, Control> row = (label, c) =>
            {
                g.Controls.Add(new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Right, Margin = new Padding(Ui.S(4), Ui.S(7), Ui.S(8), Ui.S(4)) });
                c.Margin = new Padding(Ui.S(4));
                g.Controls.Add(c);
            };
            home = new TextBox { Text = (string)s["home_url"], Width = Ui.S(320) };
            row("시작 페이지", home);
            engine = Combo(Core.SearchEngines.ToDictionary(kv => kv.Key, kv => kv.Value[0]), (string)s["search_engine"]);
            row("검색 엔진", engine);
            startMode = Combo(Core.StartModes, (string)s["start_mode"]);
            row("시작할 때", startMode);
            saveHistory = new CheckBox { Text = "방문 기록·마지막 탭 저장 (끄면 새 기록만 중단)", AutoSize = true, Checked = (bool)s["save_history"] };
            row("기록", saveHistory);
            allowDownloads = new CheckBox { Text = "다운로드 허용 (끄면 차단)", AutoSize = true, Checked = (bool)s["allow_downloads"] };
            row("다운로드", allowDownloads);
            capture = Combo(Core.CaptureActions, (string)s["capture_tool_action"]);
            row("녹화·원격 프로그램 감지 시", capture);
            blur = new CheckBox { Text = "다른 창으로 전환하면 화면 가리기", AutoSize = true, Checked = (bool)s["blur_on_inactive"] };
            row("화면 보호", blur);
            checkUpdates = new CheckBox { Text = "하루 한 번 새 버전 확인 (GitHub에 접속)", AutoSize = true, Checked = (bool)s["check_updates"] };
            row("업데이트", checkUpdates);
            var upd = new Button { Text = "지금 새 버전 확인", AutoSize = true };
            upd.Click += (x, e) => w.CheckForUpdates(true);
            row("", upd);
            var folder = new Button { Text = "저장 폴더 열기 (설정·로그 위치)", AutoSize = true };
            folder.Click += (x, e) => w.OpenDataFolder();
            row("", folder);
            var info = new Label { Text = App.Name + " v" + App.Version + "\n개발자: " + App.Author, AutoSize = true, ForeColor = Ui.Hex("#636e72"), Margin = new Padding(Ui.S(4), Ui.S(10), Ui.S(4), Ui.S(4)) };
            g.Controls.Add(info);
            g.SetColumnSpan(info, 2);
            var ok = new Button { Text = "저장", DialogResult = DialogResult.OK, AutoSize = true };
            var cancel = new Button { Text = "취소", DialogResult = DialogResult.Cancel, AutoSize = true };
            var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill };
            buttons.Controls.Add(cancel);
            buttons.Controls.Add(ok);
            g.Controls.Add(buttons);
            g.SetColumnSpan(buttons, 2);
            Controls.Add(g);
            AcceptButton = ok;
            CancelButton = cancel;
        }

        public Dictionary<string, object> Values()
        {
            string h = home.Text.Trim();
            if (h == "") h = (string)Core.DefaultSettings()["home_url"];
            return new Dictionary<string, object>
            {
                { "home_url", Core.HasScheme(h) ? h : Core.ResolveInput(h) },
                { "search_engine", Key(Core.SearchEngines.ToDictionary(kv => kv.Key, kv => kv.Value[0]), engine) },
                { "start_mode", Key(Core.StartModes, startMode) },
                { "save_history", saveHistory.Checked }, { "allow_downloads", allowDownloads.Checked },
                { "capture_tool_action", Key(Core.CaptureActions, capture) }, { "blur_on_inactive", blur.Checked },
                { "check_updates", checkUpdates.Checked },
            };
        }
    }
}
