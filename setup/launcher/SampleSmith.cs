// SampleSmith.exe: starts the app, or runs setup in its own window when something is missing.
// Build (any Windows, no extra tools):
//   C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:winexe /win32icon:app\icon.ico /out:SampleSmith.exe setup\launcher\SampleSmith.cs
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

static class Launcher
{
    // find_spec only locates modules, so this check takes a fraction of a second instead of importing torch.
    const string Check = "import sys, importlib.util as u; sys.exit(any(u.find_spec(m) is None for m in 'webview clr sounddevice soundfile yt_dlp imageio_ffmpeg librosa torch demucs omegaconf ml_collections deeprhythm skey nnAudio einops'.split()))";

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    static void Fail(string text)
    {
        MessageBox.Show(text, "SampleSmith", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    // The venv's python.exe and pythonw.exe both end up in a console Python, which opens a cmd window.
    // Starting python.exe with CreateNoWindow gives it a hidden console that its child inherits, so nothing shows.
    static Process Hidden(string exe, string args, string dir)
    {
        var p = new ProcessStartInfo(exe, args);
        p.UseShellExecute = false;
        p.CreateNoWindow = true;
        p.WorkingDirectory = dir;
        return Process.Start(p);
    }

    [STAThread]
    static void Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string py = Path.Combine(dir, @"runtime\venv\Scripts\python.exe");
        string engine = Path.Combine(dir, @"runtime\engine\inference.py");
        string script = Path.Combine(dir, @"app\samplesmith.py");
        string setup = Path.Combine(dir, @"setup\install.ps1");

        if (!File.Exists(script))
        {
            Fail("The app folder is missing. Keep SampleSmith.exe in the folder it came in, next to the app and setup folders.");
            return;
        }

        bool ready = File.Exists(py) && File.Exists(engine);
        if (ready)
        {
            try
            {
                var c = Hidden(py, "-c \"" + Check + "\"", dir);
                c.WaitForExit();
                ready = c.ExitCode == 0;
            }
            catch (Exception) { ready = false; }
        }

        try
        {
            if (!ready)
            {
                // First run (or something went missing): setup runs hidden behind its own window,
                // and the script starts the app when it's done.
                if (!File.Exists(setup))
                {
                    Fail("SampleSmith isn't set up and setup\\install.ps1 is missing. Download SampleSmith again.");
                    return;
                }
                SetProcessDPIAware();
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new SetupWindow(dir, setup));
                return;
            }
            Hidden(py, "\"" + script + "\"", dir);
        }
        catch (Exception e)
        {
            Fail("Couldn't start SampleSmith: " + e.Message);
        }
    }
}

// Setup in the app's look: the pad logo (its light steps round while working), one gold progress bar,
// install.ps1's nine steps as they happen, and Cancel / Show details. On a failure it says why and
// offers Try again. install.ps1 runs hidden; its "[n/9] ..." lines drive the window.
class SetupWindow : Form
{
    static readonly string[] Steps = {
        "Checking your graphics card", "Checking Windows components", "Getting the installer tools",
        "Setting up Python 3.11", "Installing PyTorch", "Installing the app packages",
        "Getting the separation engine", "Setting up key detection", "Finishing up" };
    static readonly int[] Loop = { 0, 1, 3, 2 };  // the lit pad goes round clockwise

    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    readonly string dir, script;
    readonly float s;  // DPI scale
    readonly FontFamily word;
    readonly FontStyle wordStyle;
    readonly string[] notes = new string[9];
    readonly StringBuilder log = new StringBuilder();
    readonly Timer anim = new Timer();
    readonly TextBox details = new TextBox();
    readonly Key cancelKey, detailsKey, retryKey;
    Process proc;
    int step, frame;
    bool failed, done, stopping;
    string failMsg, lastText;
    string size = "2 to 4 GB";  // until install.ps1 has found the graphics card

    public SetupWindow(string dir, string script)
    {
        this.dir = dir;
        this.script = script;
        using (var g = CreateGraphics()) s = g.DpiX / 96f;

        var fonts = new PrivateFontCollection();
        string ttf = Path.Combine(dir, @"app\fonts\InstrumentSans-700.ttf");
        try { if (File.Exists(ttf)) fonts.AddFontFile(ttf); } catch (Exception) { }
        word = fonts.Families.Length > 0 ? fonts.Families[0] : new FontFamily("Segoe UI");
        wordStyle = word.IsStyleAvailable(FontStyle.Regular) ? FontStyle.Regular : FontStyle.Bold;
        if (fonts.Families.Length == 0) wordStyle = FontStyle.Bold;

        Text = "SampleSmith Setup";
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.None;
        BackColor = C(0x0d0d12);
        DoubleBuffered = true;
        ClientSize = new Size(P(560), P(620));
        Font = new Font("Segoe UI", 10f * s, GraphicsUnit.Pixel);

        cancelKey = AddKey("Cancel", false, false);
        retryKey = AddKey("Try again", true, false);
        detailsKey = AddKey("Show details", false, true);
        cancelKey.Click += delegate { if (failed || done) Close(); else Stop(); };
        retryKey.Click += delegate { Begin(); };
        detailsKey.Click += delegate { ToggleDetails(); };

        details.Multiline = true;
        details.ReadOnly = true;
        details.ScrollBars = ScrollBars.Vertical;
        details.BorderStyle = BorderStyle.None;
        details.BackColor = C(0x0a0a10);
        details.ForeColor = C(0x9a9aa4);
        details.Font = new Font("Consolas", 9f * s, GraphicsUnit.Point);
        details.Visible = false;
        Controls.Add(details);

        anim.Interval = 400;
        anim.Tick += delegate { frame++; Invalidate(); };
        Arrange();
    }

    int P(float v) { return (int)Math.Round(v * s); }
    static Color C(int rgb) { return C(rgb, 255); }
    static Color C(int rgb, int a) { return Color.FromArgb(a, (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255); }

    Key AddKey(string text, bool lit, bool ghost)
    {
        var k = new Key(text, s);
        k.Lit = lit;
        k.Ghost = ghost;
        Controls.Add(k);
        return k;
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        // dark title bar in the window's own colour (Windows 11; dark on 10)
        int on = 1, caption = 0x00120D0D, text = 0x00F0F4F4;
        DwmSetWindowAttribute(Handle, 20, ref on, 4);
        DwmSetWindowAttribute(Handle, 35, ref caption, 4);
        DwmSetWindowAttribute(Handle, 34, ref caption, 4);
        DwmSetWindowAttribute(Handle, 36, ref text, 4);
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        Begin();
    }

    void Begin()
    {
        failed = done = stopping = false;
        failMsg = lastText = null;
        step = 0;
        for (int i = 0; i < notes.Length; i++) notes[i] = null;
        log.Length = 0;
        details.Text = "";
        var p = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"");
        p.UseShellExecute = false;
        p.CreateNoWindow = true;
        p.WorkingDirectory = dir;
        p.RedirectStandardOutput = true;
        p.RedirectStandardError = true;
        p.EnvironmentVariables["SAMPLESMITH_NOPAUSE"] = "1";  // a failure ends the script instead of waiting for Enter
        proc = new Process();
        proc.StartInfo = p;
        proc.EnableRaisingEvents = true;
        proc.OutputDataReceived += (o, ev) => { if (ev.Data != null) BeginInvoke(new Action<string>(OnLine), ev.Data); };
        proc.ErrorDataReceived += (o, ev) => { if (ev.Data != null) BeginInvoke(new Action<string>(Log), ev.Data); };
        proc.Exited += (o, ev) => BeginInvoke(new Action(OnExit));
        try
        {
            proc.Start();
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
        }
        catch (Exception ex)
        {
            failed = true;
            failMsg = "Couldn't start setup: " + ex.Message;
        }
        anim.Enabled = !failed;
        Arrange();
        Invalidate();
    }

    void Log(string line)
    {
        log.AppendLine(line);
        if (details.Visible) details.AppendText(line + Environment.NewLine);
    }

    void OnLine(string line)
    {
        Log(line);
        var m = Regex.Match(line, @"^\[(\d)/9\]\s*(.*)$");
        if (m.Success)
        {
            step = int.Parse(m.Groups[1].Value);
            Invalidate();
            return;
        }
        string t = line.Trim();
        if (t.Length == 0) return;
        if (t.StartsWith("@@size "))
        {
            size = t.Substring(7);
            Invalidate();
            return;
        }
        if (t.StartsWith("Check your internet connection")) failMsg = lastText;  // the line before it says what failed
        else if (t == "SampleSmith is ready.") done = true;
        else if (step > 0 && line.StartsWith("  ") && t != "OK") notes[step - 1] = t;
        lastText = t;
        Invalidate();
    }

    void OnExit()
    {
        anim.Enabled = false;
        if (stopping) return;
        if (proc.ExitCode == 0)
        {
            done = true;  // the script has started the app; this window goes
            var close = new Timer();
            close.Interval = 900;
            close.Tick += delegate { close.Stop(); Close(); };
            close.Start();
        }
        else
        {
            failed = true;
            if (failMsg == null) failMsg = "Setup stopped before it finished.";
        }
        Arrange();
        Invalidate();
    }

    bool Running { get { return proc != null && !failed && !done && !stopping; } }

    void Stop()
    {
        stopping = true;
        try { Process.Start(new ProcessStartInfo("taskkill", "/T /F /PID " + proc.Id) { CreateNoWindow = true, UseShellExecute = false }); }
        catch (Exception) { }
        Close();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (Running)
        {
            var answer = MessageBox.Show(this, "Stop setup? You can finish it later by opening SampleSmith again.",
                                         "SampleSmith", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) { e.Cancel = true; return; }
            stopping = true;
            try { Process.Start(new ProcessStartInfo("taskkill", "/T /F /PID " + proc.Id) { CreateNoWindow = true, UseShellExecute = false }); }
            catch (Exception) { }
        }
        base.OnFormClosing(e);
    }

    void ToggleDetails()
    {
        details.Visible = !details.Visible;
        if (details.Visible)
        {
            details.Text = log.ToString();
            details.SelectionStart = details.TextLength;
            details.ScrollToCaret();
        }
        detailsKey.Text = details.Visible ? "Hide details" : "Show details";
        Arrange();
        Invalidate();
    }

    void Arrange()
    {
        ClientSize = new Size(P(560), P(details.Visible ? 810 : 620));
        int y = P(536), h = P(40);
        retryKey.Visible = failed;
        cancelKey.Visible = !done;
        cancelKey.Text = failed ? "Close" : "Cancel";
        if (failed)
        {
            retryKey.Bounds = new Rectangle(P(48), y, P(116), h);
            cancelKey.Bounds = new Rectangle(P(176), y, P(92), h);
        }
        else cancelKey.Bounds = new Rectangle(P(48), y, P(96), h);
        detailsKey.Bounds = new Rectangle(P(512 - 112), y, P(112), h);
        details.Bounds = new Rectangle(P(48), P(592), P(464), P(170));
        foreach (Control k in Controls) k.Invalidate();
    }

    static GraphicsPath Round(RectangleF r, float rad)
    {
        var p = new GraphicsPath();
        float d = Math.Min(rad * 2, Math.Min(r.Width, r.Height));
        if (d <= 0) { p.AddRectangle(r); return p; }
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    RectangleF R(float x, float y, float w, float h) { return new RectangleF(x * s, y * s, w * s, h * s); }

    void Fill(Graphics g, RectangleF r, float rad, Color c)
    {
        using (var p = Round(r, rad * s)) using (var b = new SolidBrush(c)) g.FillPath(b, p);
    }

    // one raised pad of the logo: a face over a darker wall; lit pads are gold and pressed down a pixel
    void Pad(Graphics g, float x, float y, float size, bool lit, int alpha)
    {
        float press = lit ? 1 : 0;
        Fill(g, R(x, y + press + (lit ? 1 : 2), size, size), size * 0.25f, C(lit ? 0x8f7420 : 0x8e8e98, alpha));
        Fill(g, R(x, y + press, size, size), size * 0.25f, C(lit ? 0xf3c94a : 0xececf0, alpha));
    }

    void Write(Graphics g, string text, Font f, Color c, RectangleF r, StringAlignment align)
    {
        using (var b = new SolidBrush(c))
        using (var fmt = new StringFormat())
        {
            fmt.Alignment = align;
            fmt.LineAlignment = StringAlignment.Near;
            fmt.Trimming = StringTrimming.EllipsisCharacter;
            g.DrawString(text, f, b, r, fmt);
        }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.AntiAlias;
        float W = ClientSize.Width, H = ClientSize.Height;

        // soft gold glow in the top-left corner, like the app
        using (var glow = new GraphicsPath())
        {
            glow.AddEllipse(-W * 0.55f, -H * 0.5f, W * 1.1f, H * 1.0f);
            using (var b = new PathGradientBrush(glow))
            {
                b.CenterPoint = new PointF(0, 0);
                b.CenterColor = C(0xf3c94a, 34);
                b.SurroundColors = new[] { C(0xf3c94a, 0) };
                g.FillPath(b, glow);
            }
        }

        // logo
        int lit = done || (!failed && proc == null) ? 1 : failed ? -1 : Loop[frame % 4];
        int alpha = failed ? 140 : 255;
        for (int i = 0; i < 4; i++) Pad(g, 48 + (i % 2) * 23, 40 + (i / 2) * 23, 18, i == lit, alpha);
        using (var f = new Font(word, 30 * s, wordStyle, GraphicsUnit.Pixel))
            Write(g, "samplesmith", f, C(0xf4f4f0), R(100, 38, 420, 44), StringAlignment.Near);

        // what's happening
        string title = failed ? "Setup stopped" : done ? "SampleSmith is ready" : "Setting things up";
        string sub = failed ? failMsg : "This happens once. It downloads " + size + ".";
        using (var f = new Font(word, 20 * s, wordStyle, GraphicsUnit.Pixel))
            Write(g, title, f, C(0xf4f4f0), R(48, 108, 464, 30), StringAlignment.Near);
        using (var f = new Font("Segoe UI", 13 * s, GraphicsUnit.Pixel))
            Write(g, sub ?? "", f, failed ? C(0xf2a6a6) : C(0xb4b4bc), R(48, 140, 464, 40), StringAlignment.Near);

        // one progress bar for the whole setup
        string now = step > 0 ? Steps[step - 1] : "Starting";
        using (var f = new Font("Segoe UI", 12.5f * s, GraphicsUnit.Pixel))
        {
            Write(g, done ? "Done" : now, f, C(0xb4b4bc), R(48, 184, 340, 20), StringAlignment.Near);
            Write(g, done ? "" : "Step " + Math.Max(step, 1) + " of 9", f, C(0x85858f), R(312, 184, 200, 20), StringAlignment.Far);
        }
        Fill(g, R(48, 208, 464, 8), 4, C(0x0a0a10));
        float part = done ? 1f : Math.Max(0.02f, (Math.Max(step, 1) - 0.5f) / 9f);
        var bar = R(48, 208, 464 * part, 8);
        using (var p = Round(bar, 4 * s))
        using (var b = new LinearGradientBrush(new RectangleF(bar.X, bar.Y, Math.Max(1, bar.Width), bar.Height),
                                               failed ? C(0xe2716c) : C(0xe2b33a), failed ? C(0xf39a95) : C(0xf3c94a), 0f))
            g.FillPath(b, p);

        // the nine steps
        Fill(g, R(48, 234, 464, 9 * 30 + 12), 16, C(0x1c1c28, 160));
        using (var f = new Font("Segoe UI", 13.5f * s, GraphicsUnit.Pixel))
        using (var nf = new Font("Segoe UI", 11.5f * s, GraphicsUnit.Pixel))
        {
            for (int i = 0; i < 9; i++)
            {
                float y = 240 + i * 30;
                bool isDone = done || i < step - 1;
                bool isNow = !done && i == step - 1;
                if (isDone)
                {
                    Fill(g, R(60, y + 6, 18, 18), 5, C(0xf3c94a));
                    using (var pen = new Pen(C(0x141209), 2f * s))
                    {
                        pen.StartCap = pen.EndCap = LineCap.Round;
                        g.DrawLines(pen, new[] { new PointF(64.5f * s, (y + 15) * s), new PointF(67.5f * s, (y + 18) * s), new PointF(73.5f * s, (y + 11) * s) });
                    }
                }
                else if (isNow && failed) Fill(g, R(60, y + 6, 18, 18), 5, C(0xe2716c));
                else if (isNow)
                    for (int k = 0; k < 4; k++)
                        Fill(g, R(61 + (k % 2) * 9, y + 7 + (k / 2) * 9, 7, 7), 2, k == Loop[frame % 4] ? C(0xf3c94a) : C(0xececf0));
                else Fill(g, R(60, y + 6, 18, 18), 5, C(0x0a0a10));
                Color ink = isNow ? C(0xf4f4f0) : isDone ? C(0xb4b4bc) : C(0x85858f);
                Write(g, Steps[i], f, ink, R(88, y + 5, 260, 22), StringAlignment.Near);
                if ((isNow || isDone) && notes[i] != null)
                    Write(g, notes[i], nf, C(0x85858f), R(300, y + 7, 200, 20), StringAlignment.Far);
            }
        }

        using (var f = new Font("Segoe UI", 11f * s, GraphicsUnit.Pixel))
            Write(g, "Made by Trill · © 2026", f, C(0x85858f, 200), new RectangleF(0, H - 26 * s, W, 20 * s), StringAlignment.Center);
    }
}

// A raised key like the app's buttons: a face over a darker wall that presses in; Lit is the gold key,
// Ghost is a plain text button.
class Key : Control
{
    public bool Lit, Ghost;
    readonly float s;
    bool hover, down;

    public Key(string text, float s)
    {
        this.s = s;
        Text = text;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer |
                 ControlStyles.SupportsTransparentBackColor | ControlStyles.Selectable, true);
        BackColor = Color.Transparent;
        Cursor = Cursors.Hand;
        Font = new Font("Segoe UI", 13.5f * s, FontStyle.Regular, GraphicsUnit.Pixel);
        TabStop = true;
    }

    protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hover = down = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Enter) OnClick(EventArgs.Empty);
        base.OnKeyDown(e);
    }

    static Color C(int rgb) { return Color.FromArgb(255, (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255); }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.AntiAlias;
        float wall = 3 * s, press = down ? 2 * s : 0;
        var face = new RectangleF(0, press, Width - 1, Height - wall - 1);
        if (!Ghost)
        {
            using (var p = Round(new RectangleF(0, wall - (down ? s : 0), Width - 1, Height - wall - 1), 11 * s))
            using (var b = new SolidBrush(Lit ? C(0x8f7420) : C(0x121218)))
                g.FillPath(b, p);
            Color top = Lit ? (hover ? C(0xf8da7a) : C(0xf6d261)) : (hover ? C(0x34343f) : C(0x30303c));
            Color bottom = Lit ? (hover ? C(0xf1c844) : C(0xefc23a)) : (hover ? C(0x2a2a35) : C(0x262631));
            using (var p = Round(face, 11 * s))
            using (var b = new LinearGradientBrush(face, top, bottom, 90f))
                g.FillPath(b, p);
        }
        else if (Focused) using (var p = Round(face, 11 * s)) using (var pen = new Pen(Color.FromArgb(120, 243, 201, 74), s)) g.DrawPath(pen, p);
        Color ink = Lit ? C(0x141209) : Ghost ? (hover ? C(0xf4f4f0) : C(0xb4b4bc)) : C(0xf4f4f0);
        using (var b = new SolidBrush(ink))
        using (var fmt = new StringFormat())
        {
            fmt.Alignment = StringAlignment.Center;
            fmt.LineAlignment = StringAlignment.Center;
            g.DrawString(Text, Font, b, face, fmt);
        }
    }

    static GraphicsPath Round(RectangleF r, float rad)
    {
        var p = new GraphicsPath();
        float d = Math.Min(rad * 2, Math.Min(r.Width, r.Height));
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }
}
