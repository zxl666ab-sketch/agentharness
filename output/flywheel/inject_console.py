
import ctypes, sys
from ctypes import wintypes
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
KEY_EVENT = 1
VK_RETURN = 0x0D
class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [('bKeyDown', wintypes.BOOL),('wRepeatCount', wintypes.WORD),('wVirtualKeyCode', wintypes.WORD),('wVirtualScanCode', wintypes.WORD),('uChar', wintypes.WCHAR),('dwControlKeyState', wintypes.DWORD)]
class INPUT_RECORD(ctypes.Structure):
    _fields_ = [('EventType', wintypes.WORD),('_pad', wintypes.WORD),('Event', KEY_EVENT_RECORD)]
def send_text(h, text):
    for ch in text + '\r':
        for down in (True, False):
            rec = INPUT_RECORD(); rec.EventType = KEY_EVENT; rec.Event.bKeyDown = down; rec.Event.wRepeatCount = 1
            rec.Event.wVirtualKeyCode = VK_RETURN if ch == '\r' else (ord(ch.upper()) if ch.isalpha() else 0)
            rec.Event.uChar = ch if ch != '\r' else '\r'
            written = wintypes.DWORD(0)
            if not kernel32.WriteConsoleInputW(h, ctypes.byref(rec), 1, ctypes.byref(written)):
                raise ctypes.WinError(ctypes.get_last_error())
def main():
    pid = int(sys.argv[1]); text = sys.argv[2]
    kernel32.FreeConsole();
    if not kernel32.AttachConsole(pid):
        print('AttachConsole failed', ctypes.get_last_error()); return 1
    kernel32.SetConsoleCtrlHandler(None, True)
    h = kernel32.GetStdHandle(-10)
    send_text(h, text)
    print('INJECT_OK', pid, repr(text[:100]))
    kernel32.FreeConsole(); return 0
if __name__ == '__main__':
    raise SystemExit(main())
