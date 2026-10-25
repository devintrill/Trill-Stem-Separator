package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"unsafe"
)

// find_spec only locates modules, so this check takes a fraction of a second instead of importing torch.
const check = `import sys, importlib.util as u
sys.exit(any(u.find_spec(m) is None for m in "webview clr sounddevice soundfile yt_dlp imageio_ffmpeg librosa torch demucs omegaconf ml_collections deeprhythm skey nnAudio einops".split()))`

func messageBox(text string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	t, _ := syscall.UTF16PtrFromString(text)
	c, _ := syscall.UTF16PtrFromString("SampleSmith")
	user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(c)), 0x10)
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func main() {
	exe, _ := os.Executable()
	dir := filepath.Dir(exe)
	py := filepath.Join(dir, `runtime\venv\Scripts\python.exe`)
	pyw := filepath.Join(dir, `runtime\venv\Scripts\pythonw.exe`)
	engine := filepath.Join(dir, `runtime\engine\inference.py`)
	script := filepath.Join(dir, `app\samplesmith.py`)
	setup := filepath.Join(dir, `setup\install.ps1`)

	if !exists(script) {
		messageBox("The app folder is missing. Keep SampleSmith.exe in the folder it came in, next to the app and setup folders.")
		return
	}

	ready := exists(pyw) && exists(engine)
	if ready {
		c := exec.Command(py, "-c", check)
		c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
		ready = c.Run() == nil
	}
	if !ready {
		// First run (or something went missing): setup shows its own window and starts the app when done.
		if !exists(setup) {
			messageBox("SampleSmith isn't set up and setup\\install.ps1 is missing. Download SampleSmith again.")
			return
		}
		s := exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", setup)
		s.Dir = dir
		s.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x00000010} // CREATE_NEW_CONSOLE
		if err := s.Start(); err != nil {
			messageBox("Couldn't start setup: " + err.Error())
		}
		return
	}

	a := exec.Command(pyw, script)
	a.Dir = dir
	if err := a.Start(); err != nil {
		messageBox("Couldn't start SampleSmith: " + err.Error())
	}
}
