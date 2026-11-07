"""Icône dans la zone de notification de Windows (à côté de l'horloge) — sans dépendance externe (ctypes).

- Tray(icon_path, tooltip, on_show, on_quit) : icône + menu clic droit (Ouvrir / Quitter).
- acquire_single_instance() : une seule instance du lanceur ; un second lancement réveille la première.
"""
import ctypes
import os
import threading
from ctypes import wintypes

CLASS = "AGOA_Launcher_Tray_Window"
MUTEX = "Local\\AGOA_Launcher_Singleton"
WM_USER = 0x0400
WM_TRAY = WM_USER + 1
WM_SHOW = WM_USER + 2
WM_CLOSE, WM_DESTROY, WM_NULL = 0x0010, 0x0002, 0x0000
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
ID_OPEN, ID_QUIT = 1, 2

if os.name == "nt":
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE), ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wintypes.HANDLE)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.LPVOID]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

_mutex = None


def acquire_single_instance():
    """True si c'est la première instance ; sinon réveille la première (fenêtre au premier plan) et renvoie False."""
    global _mutex
    if os.name != "nt":
        return True
    _mutex = kernel32.CreateMutexW(None, False, MUTEX)
    if ctypes.get_last_error() == 183:     # ERROR_ALREADY_EXISTS
        try:
            user32.AllowSetForegroundWindow(-1)
            hwnd = user32.FindWindowW(CLASS, None)
            if hwnd:
                user32.PostMessageW(hwnd, WM_SHOW, 0, 0)
        except Exception:
            pass
        return False
    return True


class Tray:
    def __init__(self, icon_path, tooltip, on_show, on_quit):
        self.icon_path, self.tooltip = icon_path, tooltip
        self.on_show, self.on_quit = on_show, on_quit
        self.hwnd = None
        self.hicon = None
        self.alive = False
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)           # référence conservée (sinon ramassé par le GC -> plantage)
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)

    # --- Win32 -----------------------------------------------------------
    def _data(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip[:127]
        return nid

    def _add(self):
        self.alive = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP))))

    def _run(self):
        try:
            self.hicon = user32.LoadImageW(None, self.icon_path, 1, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50), 0x10)
        except Exception:
            self.hicon = None
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = CLASS
        user32.RegisterClassW(ctypes.byref(wc))
        self.wm_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")      # explorateur relancé : on remet l'icône
        self.hwnd = user32.CreateWindowExW(0, CLASS, "AGOA Launcher", 0, 0, 0, 0, 0, None, None, hinst, None)
        self._add()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _menu(self):
        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, 0, ID_OPEN, "Ouvrir AGOA Launcher")
        user32.AppendMenuW(m, 0x800, 0, None)          # séparateur
        user32.AppendMenuW(m, 0, ID_QUIT, "Quitter")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(m, 0x100 | 0x2, pt.x, pt.y, 0, self.hwnd, None)      # TPM_RETURNCMD | TPM_RIGHTBUTTON
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd == ID_OPEN:
            self.on_show()
        elif cmd == ID_QUIT:
            self.on_quit()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_show()
                elif ev == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_SHOW:
                self.on_show()
                return 0
            if msg == getattr(self, "wm_taskbar", -1):
                self._add()
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # --- API -------------------------------------------------------------
    def notify(self, title, text):
        """Bulle d'information près de l'horloge."""
        if not self.alive:
            return
        nid = self._data(NIF_INFO)
        nid.szInfo = text[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = 1                       # NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def stop(self):
        """Retire l'icône (sinon elle reste affichée jusqu'au survol de la souris)."""
        try:
            if self.alive:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))
                self.alive = False
            if self.hwnd:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
