// 크롬(라이트 테마)과 비슷한 화면 부품: 탭 줄, 둥근 버튼, 둥근 주소창

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace YDBrowser
{
    static class Ui
    {
        public static readonly float Scale = GetScale();
        public static readonly Font Font = new Font("Malgun Gothic", 9f);
        public static readonly Color Text = Hex("#202124"), Sub = Hex("#5f6368"), Blue = Hex("#1a73e8"), Line = Hex("#dadce0");

        static float GetScale()
        {
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) return g.DpiX / 96f;
        }

        public static int S(int px) { return (int)Math.Round(px * Scale); }
        public static Color Hex(string h) { return ColorTranslator.FromHtml(h); }

        public static GraphicsPath Round(Rectangle r, int radius, bool topOnly = false)
        {
            var p = new GraphicsPath();
            int d = Math.Max(1, Math.Min(radius * 2, Math.Min(r.Width, r.Height)));
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            if (topOnly)
            {
                p.AddLine(r.Right, r.Bottom, r.X, r.Bottom);
            }
            else
            {
                p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
                p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            }
            p.CloseFigure();
            return p;
        }
    }

    // 글자 하나짜리 둥근 버튼 (← → ⟳ ⋮ ☆ 등). Round=false 면 둥근 사각형(북마크바)
    class IconButton : Control
    {
        bool hover, down;
        public bool Round = true;
        public Color Fore = Ui.Sub;
        readonly ToolTip tip = new ToolTip();

        public IconButton(string text, string tooltip, int size, float fontPt)
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint |
                ControlStyles.SupportsTransparentBackColor | ControlStyles.ResizeRedraw, true);
            SetStyle(ControlStyles.Selectable, false);
            Text = text;
            Font = new Font("Segoe UI Symbol", fontPt);
            Size = new Size(Ui.S(size), Ui.S(size));
            BackColor = Color.Transparent;
            Cursor = Cursors.Default;
            Tip = tooltip;
        }

        public string Tip { set { tip.SetToolTip(this, value); } }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = e.Button == MouseButtons.Left; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new Rectangle(0, 0, Width - 1, Height - 1);
            if (Enabled && (hover || down))
                using (var b = new SolidBrush(Ui.Hex(down ? "#dadce0" : "#e8eaed")))
                {
                    if (Round) g.FillEllipse(b, r);
                    else using (var p = Ui.Round(r, Ui.S(4))) g.FillPath(b, p);
                }
            TextRenderer.DrawText(g, Text, Font, ClientRectangle, Enabled ? Fore : Ui.Hex("#c4c7cc"),
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine);
        }
    }

    // 크롬처럼 주소 줄 '위'에 놓이는 탭 줄. 그리기와 마우스 처리만 하고, 탭 목록은 창이 관리한다.
    class TabStrip : Control
    {
        public List<Tab> Tabs;
        public int Current = -1;
        int hover = -1, dragFrom = -1;
        bool hoverClose, hoverNew;
        readonly ToolTip tip = new ToolTip();
        string tipText = "";
        public event Action<int> Selected, CloseRequested;
        public event Action<int, int> Moved;
        public event Action NewRequested;
        public event Action<int, Point> MenuRequested;

        public TabStrip()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            SetStyle(ControlStyles.Selectable, false);
            Height = Ui.S(40);
            BackColor = Ui.Hex("#dee1e6");
            Font = Ui.Font;
        }

        int TabWidth()
        {
            int n = Math.Max(1, Tabs.Count);
            return Math.Max(Ui.S(44), Math.Min(Ui.S(220), (Width - Ui.S(16) - Ui.S(40)) / n));
        }

        public Rectangle TabRect(int i)
        {
            int w = TabWidth();
            return new Rectangle(Ui.S(8) + i * w, Ui.S(6), w - Ui.S(2), Height - Ui.S(6));
        }

        Rectangle CloseRect(int i)
        {
            var r = TabRect(i);
            int s = Ui.S(18);
            return new Rectangle(r.Right - Ui.S(8) - s, r.Top + (r.Height - s) / 2, s, s);
        }

        Rectangle NewRect()
        {
            int x = Tabs.Count == 0 ? Ui.S(8) : TabRect(Tabs.Count - 1).Right + Ui.S(4);
            int s = Ui.S(28);
            return new Rectangle(x, Ui.S(6) + (Height - Ui.S(6) - s) / 2, s, s);
        }

        int HitTab(Point p)
        {
            for (int i = 0; i < Tabs.Count; i++) if (TabRect(i).Contains(p)) return i;
            return -1;
        }

        bool ShowClose(int i) { return i == Current || i == hover || TabRect(i).Width >= Ui.S(90); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(BackColor);
            for (int i = 0; i < Tabs.Count; i++)
            {
                var r = TabRect(i);
                if (i == Current || i == hover)
                    using (var b = new SolidBrush(i == Current ? Color.White : Ui.Hex("#eceff3")))
                    using (var p = Ui.Round(r, Ui.S(9), true)) g.FillPath(b, p);
                int x = r.X + Ui.S(10);
                var t = Tabs[i];
                if (t.Icon != null && r.Width >= Ui.S(60))
                {
                    int s = Ui.S(16);
                    g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    g.DrawImage(t.Icon, new Rectangle(x, r.Top + (r.Height - s) / 2, s, s));
                    x += s + Ui.S(6);
                }
                int right = ShowClose(i) ? CloseRect(i).Left - Ui.S(2) : r.Right - Ui.S(6);
                TextRenderer.DrawText(g, t.Title, Font, new Rectangle(x, r.Top, Math.Max(0, right - x), r.Height),
                    i == Current ? Ui.Text : Ui.Sub, TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine);
                if (ShowClose(i))
                {
                    var c = CloseRect(i);
                    if (i == hover && hoverClose)
                        using (var b = new SolidBrush(Ui.Hex("#cfd2d7"))) g.FillEllipse(b, c);
                    using (var f = new Font("Segoe UI Symbol", 7f))
                        TextRenderer.DrawText(g, "✕", f, c, Ui.Sub, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
                }
            }
            var n = NewRect();
            if (hoverNew) using (var b = new SolidBrush(Ui.Hex("#cfd2d7"))) g.FillEllipse(b, n);
            using (var f = new Font("Segoe UI", 13f))
                TextRenderer.DrawText(g, "+", f, n, Ui.Sub, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            int h = HitTab(e.Location);
            bool hc = h >= 0 && ShowClose(h) && CloseRect(h).Contains(e.Location), hn = NewRect().Contains(e.Location);
            if (dragFrom >= 0 && e.Button == MouseButtons.Left && h >= 0 && h != dragFrom)
            {
                var m = Moved;   // 끌어서 순서 바꾸기
                if (m != null) m(dragFrom, h);
                dragFrom = h;
            }
            if (h != hover || hc != hoverClose || hn != hoverNew)
            {
                hover = h; hoverClose = hc; hoverNew = hn;
                Invalidate();
            }
            string t = h >= 0 ? (hc ? "탭 닫기 (Ctrl+W)" : Tabs[h].Title) : hn ? "새 탭 (Ctrl+T)" : "";
            if (t != tipText) { tipText = t; tip.SetToolTip(this, t); }
            base.OnMouseMove(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hover = -1; hoverClose = hoverNew = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            int h = HitTab(e.Location);
            if (e.Button == MouseButtons.Left && h >= 0 && !(ShowClose(h) && CloseRect(h).Contains(e.Location)))
            {
                dragFrom = h;
                var s = Selected;
                if (s != null) s(h);
            }
            base.OnMouseDown(e);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            dragFrom = -1;
            int h = HitTab(e.Location);
            if (e.Button == MouseButtons.Left)
            {
                if (h >= 0 && ShowClose(h) && CloseRect(h).Contains(e.Location)) { var c = CloseRequested; if (c != null) c(h); }
                else if (h < 0 && NewRect().Contains(e.Location)) { var n = NewRequested; if (n != null) n(); }
            }
            else if (e.Button == MouseButtons.Middle && h >= 0) { var c = CloseRequested; if (c != null) c(h); }   // 가운데 버튼으로 탭 닫기
            else if (e.Button == MouseButtons.Right && h >= 0) { var m = MenuRequested; if (m != null) m(h, PointToScreen(e.Location)); }
            base.OnMouseUp(e);
        }

        protected override void OnMouseDoubleClick(MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left && HitTab(e.Location) < 0 && !NewRect().Contains(e.Location))
            {
                var n = NewRequested;   // 빈 곳을 두 번 누르면 새 탭 (크롬과 같음)
                if (n != null) n();
            }
            base.OnMouseDoubleClick(e);
        }
    }

    // 둥근 주소창: [연결 상태] [주소 입력] [북마크 별]. 포커스가 있으면 흰 바탕 + 파란 테두리
    class Omnibox : Control
    {
        public readonly TextBox Box;
        public readonly Label Sec;
        public readonly IconButton Star;
        readonly ToolTip tip = new ToolTip();
        bool focused;

        public Omnibox()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            Height = Ui.S(38);
            BackColor = Color.White;
            Sec = new Label { AutoSize = true, Font = new Font("Segoe UI Emoji", 10f), BackColor = Color.Transparent };
            Box = new TextBox { BorderStyle = BorderStyle.None, Font = new Font("Malgun Gothic", 10.5f), ForeColor = Ui.Text };
            Star = new IconButton("☆", "이 페이지를 북마크 (Ctrl+D)", 28, 12f);
            Controls.AddRange(new Control[] { Sec, Box, Star });
            Box.GotFocus += (s, e) => { focused = true; Restyle(); };
            Box.LostFocus += (s, e) => { focused = false; Restyle(); };
            Restyle();
        }

        public void SetSecurity(string icon, string tooltip)
        {
            Sec.Text = icon;
            tip.SetToolTip(Sec, tooltip);
            PerformLayout();
        }

        void Restyle()
        {
            Box.BackColor = focused ? Color.White : Ui.Hex("#f1f3f4");
            Invalidate();
        }

        protected override void OnLayout(LayoutEventArgs e)
        {
            if (Star == null) return;   // 생성자에서 Height 를 정할 때는 아직 부품이 없다
            int x = Ui.S(12);
            Sec.Location = new Point(x, (Height - Sec.Height) / 2);
            if (Sec.Text != "") x += Sec.Width + Ui.S(2);
            Star.Location = new Point(Width - Star.Width - Ui.S(6), (Height - Star.Height) / 2);
            Box.SetBounds(x, (Height - Box.Height) / 2, Math.Max(10, Star.Left - x - Ui.S(4)), Box.Height);
            base.OnLayout(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Parent != null ? Parent.BackColor : Color.White);
            var r = new Rectangle(1, 1, Width - 3, Height - 3);
            using (var p = Ui.Round(r, r.Height / 2))
            {
                using (var b = new SolidBrush(focused ? Color.White : Ui.Hex("#f1f3f4"))) g.FillPath(b, p);
                if (focused) using (var pen = new Pen(Ui.Blue, Ui.S(2))) g.DrawPath(pen, p);
            }
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            Box.Focus();
            base.OnMouseDown(e);
        }
    }
}
