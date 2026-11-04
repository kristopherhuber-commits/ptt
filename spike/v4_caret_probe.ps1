# v4 spike (2026-11-04): can PTT find the terminal's text cursor, to draw the flag mark on it?
# Run with Windows PowerShell 5.1 (powershell.exe) while Windows Terminal has focus.
# Answered on Darklord: the Windows system caret reports nothing (hwnd 0), but UI Automation
# gives the cursor's character cell (14 x 28 px) through TextPattern.GetSelection.
# Throwaway: not product code.
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct GUITHREADINFO {
    public int cbSize; public int flags;
    public IntPtr hwndActive, hwndFocus, hwndCapture, hwndMenuOwner, hwndMoveSize, hwndCaret;
    public RECT rcCaret; }
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint tid, ref GUITHREADINFO g);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
}
"@
$h = [W]::GetForegroundWindow(); $procId = 0
$tid = [W]::GetWindowThreadProcessId($h, [ref]$procId)
$sb = New-Object Text.StringBuilder 256; [void][W]::GetClassName($h, $sb, 256)
"foreground: $((Get-Process -Id $procId).ProcessName) class=$sb"
$g = New-Object W+GUITHREADINFO; $g.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($g)
[void][W]::GetGUIThreadInfo($tid, [ref]$g)
"system caret: hwnd=$($g.hwndCaret) rect=$($g.rcCaret.L),$($g.rcCaret.T),$($g.rcCaret.R),$($g.rcCaret.B)"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$el = [Windows.Automation.AutomationElement]::FocusedElement
"UIA focused: $($el.Current.ClassName) / $($el.Current.ControlType.ProgrammaticName)"
$tp = $null
if ($el.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
  foreach ($r in $tp.GetSelection()) {
    $r2 = $r.Clone(); [void]$r2.ExpandToEnclosingUnit([Windows.Automation.Text.TextUnit]::Character)
    "UIA caret rect: " + (($r2.GetBoundingRectangles() | ForEach-Object { "$($_.X),$($_.Y) $($_.Width)x$($_.Height)" }) -join '; ')
  }
} else { "UIA: no TextPattern" }
