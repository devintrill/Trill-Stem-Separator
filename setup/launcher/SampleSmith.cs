// SampleSmith.exe: starts the app, or runs setup when something is missing.
// Build (any Windows, no extra tools):
//   C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:winexe /win32icon:app\icon.ico /out:SampleSmith.exe setup\launcher\SampleSmith.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    // find_spec only locates modules, so this check takes a fraction of a second instead of importing torch.
    const string Check = "import sys, importlib.util as u; sys.exit(any(u.find_spec(m) is None for m in 'webview clr sounddevice soundfile yt_dlp imageio_ffmpeg librosa torch demucs omegaconf ml_collections deeprhythm skey nnAudio einops'.split()))";

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
                // First run (or something went missing): setup shows its own window and starts the app when done.
                if (!File.Exists(setup))
                {
                    Fail("SampleSmith isn't set up and setup\\install.ps1 is missing. Download SampleSmith again.");
                    return;
                }
                var s = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + setup + "\"");
                s.UseShellExecute = true;
                s.WorkingDirectory = dir;
                Process.Start(s);
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
