// SheetMaker.exe - a launcher you can pin to the Windows taskbar.
//
// It runs "Start SheetMaker.bat" in the SheetMaker root folder (on the network drive), which opens
// the newest SheetMaker-x.y.z. Where the root folder is:
//  1. the exe's own folder, if "Start SheetMaker.bat" is there (exe kept in the root folder), else
//  2. the path typed in "SheetMaker folder.txt" next to the exe, if that file exists, else
//  3. the folder remembered on this computer (%LOCALAPPDATA%\SheetMaker\launcher_root.txt), else
//  4. you're asked once to find "Start SheetMaker.bat" (normal Open window: paste a path or browse).
//
// Hold Shift while starting it to pick the folder again.
//
// The .bat runs without a window. If it is still running after a few seconds it is waiting for you
// (for example asking to install missing add-ons), so its window is shown.
package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	batName  = "Start SheetMaker.bat"
	textName = "SheetMaker folder.txt" // optional, next to the exe: the folder path typed in
)

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")

	pMessageBox    = user32.NewProc("MessageBoxW")
	pGetKeyState   = user32.NewProc("GetKeyState")
	pShowWindow    = user32.NewProc("ShowWindow")
	pSetForeground = user32.NewProc("SetForegroundWindow")
	pAttachConsole = kernel32.NewProc("AttachConsole")
	pFreeConsole   = kernel32.NewProc("FreeConsole")
	pGetConsoleWnd = kernel32.NewProc("GetConsoleWindow")
)

func utf16(s string) *uint16 {
	p, _ := syscall.UTF16PtrFromString(s)
	return p
}

func msg(text string, flags uintptr) int {
	r, _, _ := pMessageBox.Call(0, uintptr(unsafe.Pointer(utf16(text))), uintptr(unsafe.Pointer(utf16("SheetMaker"))), flags)
	return int(r)
}

func hasBat(dir string) bool {
	if dir == "" {
		return false
	}
	st, err := os.Stat(filepath.Join(dir, batName))
	return err == nil && !st.IsDir()
}

func savedPath() string {
	return filepath.Join(os.Getenv("LOCALAPPDATA"), "SheetMaker", "launcher_root.txt")
}

func readSaved() string {
	b, err := os.ReadFile(savedPath())
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func save(dir string) {
	p := savedPath()
	_ = os.MkdirAll(filepath.Dir(p), 0o755)
	_ = os.WriteFile(p, []byte(dir+"\r\n"), 0o644)
}

// pickBat shows the normal Windows "Open" window to find "Start SheetMaker.bat" (through PowerShell,
// no window of its own). Unlike the folder picker, it takes a typed or pasted path (\\server\share\…
// or a drive letter) in its address bar or File name box, and browses the network normally.
func pickBat(start string) string {
	ps := `Add-Type -AssemblyName System.Windows.Forms; ` +
		`[System.Windows.Forms.Application]::EnableVisualStyles(); ` +
		`$d = New-Object System.Windows.Forms.OpenFileDialog; ` +
		`$d.Title = 'Find "Start SheetMaker.bat" in the SheetMaker folder on the network drive'; ` +
		`$d.Filter = 'SheetMaker start file|Start SheetMaker.bat|Batch files (*.bat)|*.bat|All files (*.*)|*.*'; ` +
		`$d.FileName = 'Start SheetMaker.bat'; ` +
		`$d.CheckFileExists = $true; $d.Multiselect = $false; $d.AutoUpgradeEnabled = $true; ` +
		`if ($env:SM_START -and (Test-Path -LiteralPath $env:SM_START)) { $d.InitialDirectory = $env:SM_START }; ` +
		`$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; ` +
		`if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.FileName) }`
	cmd := exec.Command("powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps)
	cmd.Env = append(os.Environ(), "SM_START="+start)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// asFolder turns a typed path into the SheetMaker folder: quotes removed, and a path to the .bat
// itself becomes its folder.
func asFolder(p string) string {
	p = strings.Trim(strings.TrimSpace(strings.TrimPrefix(p, "\ufeff")), "\"") // Notepad's BOM, quotes
	if strings.HasSuffix(strings.ToLower(p), ".bat") {
		p = filepath.Dir(p)
	}
	return p
}

// fromTextFile reads "SheetMaker folder.txt" next to the exe: the SheetMaker folder's path typed on
// its first line (e.g. \\server\share\SheetMaker or S:\SheetMaker).
func fromTextFile(exeDir string) string {
	b, err := os.ReadFile(filepath.Join(exeDir, textName))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(b), "\n") {
		if l := asFolder(line); l != "" {
			return l
		}
	}
	return ""
}

func shiftDown() bool {
	r, _, _ := pGetKeyState.Call(0x10) // VK_SHIFT
	return int16(r) < 0
}

func findRoot() string {
	exe, _ := os.Executable()
	exeDir := filepath.Dir(exe)
	if !shiftDown() {
		if hasBat(exeDir) {
			return exeDir
		}
		if t := fromTextFile(exeDir); hasBat(t) {
			return t
		}
		if s := readSaved(); hasBat(s) {
			return s
		}
		if t := fromTextFile(exeDir); t != "" {
			msg("The folder in \""+textName+"\" next to SheetMaker.exe has no \""+batName+"\" (or can't be "+
				"reached right now):\n\n"+t, 0x30)
		}
		msg("Where is SheetMaker?\n\nIn the next window, go to the SheetMaker folder on the network drive and "+
			"pick \""+batName+"\". You can paste or type the folder path (for example \\\\server\\share\\SheetMaker "+
			"or S:\\SheetMaker) into the address bar or the File name box.\n\nThis is asked once; hold Shift "+
			"while starting SheetMaker.exe to choose again.", 0x40)
	}
	start := readSaved()
	for {
		file := pickBat(start)
		if file == "" {
			return ""
		}
		dir := asFolder(file)
		if hasBat(dir) {
			save(dir)
			return dir
		}
		if msg("\""+batName+"\" isn't in that folder:\n\n"+dir+"\n\nPick \""+batName+"\" in the SheetMaker "+
			"folder (the folder with the SheetMaker-x.y.z version folders).", 0x05|0x30) != 4 { // retry/cancel
			return ""
		}
		start = dir
	}
}

func main() {
	root := findRoot()
	if root == "" {
		return
	}
	bat := filepath.Join(root, batName)
	cmd := exec.Command("cmd.exe")
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CmdLine:       `cmd.exe /c ""` + bat + `""`,
		HideWindow:    true,
		CreationFlags: 0x00000010, // CREATE_NEW_CONSOLE (hidden), so it can be shown if it needs you
	}
	cmd.Dir = os.Getenv("SystemRoot")
	if err := cmd.Start(); err != nil {
		msg("Couldn't start SheetMaker:\n\n"+err.Error(), 0x10)
		return
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
		return // normal: the .bat started SheetMaker and finished
	case <-time.After(8 * time.Second):
	}
	// still running: it's waiting for an answer (missing add-ons, an error message) - show its window
	pid := uintptr(cmd.Process.Pid)
	if r, _, _ := pAttachConsole.Call(pid); r != 0 {
		if hwnd, _, _ := pGetConsoleWnd.Call(); hwnd != 0 {
			pShowWindow.Call(hwnd, 1) // SW_SHOWNORMAL
			pSetForeground.Call(hwnd)
		}
		pFreeConsole.Call()
	}
}
