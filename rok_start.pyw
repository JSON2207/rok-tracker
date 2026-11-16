# -*- coding: utf-8 -*-
"""RoK-Tracker Launcher: Fenster mit Status, Update, Start/Neustart/Beenden und Tool-Kacheln.
Doppelklick genuegt (kein Konsolenfenster). Lege diese Datei in denselben Ordner wie rok_barbs.py.
Ohne tkinter oder bei einem Fehler im Fenster startet der Tracker wie frueher direkt."""
import hashlib, json, math, os, queue, re, struct, subprocess, sys, threading, time, traceback, urllib.request, webbrowser, zlib

UPDATE_BASIS = "https://raw.githubusercontent.com/JSON2207/rok-tracker/"
KANAELE = {"classic": "Classic (Stand 16.11.2026, Backup)", "main": "Aktuell (main)", "v2": "Neu (v2)"}   # Version = Branch auf GitHub; Ordner/Datenbank bleiben gleich
UPDATE_URL = UPDATE_BASIS + "main/"
DATEIEN = ["rok_barbs.py", "rok_diagnose.py", "rok_start.pyw"]      # fehlt eine online, wird sie uebersprungen

HIER = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HIER, "rok_barbs.py")
DATA = os.path.join(os.environ.get("APPDATA", HIER), "RoK-Tracker")
LOG = os.path.join(DATA, "update.log")
TRACKER_LOG = os.path.join(DATA, "tracker.log")
VERSION_FILE = os.path.join(DATA, "version.txt")
CONFIG = os.path.join(DATA, "launcher.json")
AUTOSTART = "--autostart" in sys.argv[1:]                    # vom Windows-Autostart: leise starten, Fenster minimiert
ARGS = [a for a in sys.argv[1:] if a != "--autostart"]
PORT = 8765
if "--port" in ARGS:
    try:
        PORT = int(ARGS[ARGS.index("--port") + 1])
    except Exception:
        pass
URL = "http://127.0.0.1:%d/" % PORT

# ---------------------------------------------------------------- Grundfunktionen (ohne Fenster)
def log(text):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass

def config_lesen():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def kanal_lesen():
    k = config_lesen().get("kanal")
    return k if k in KANAELE else "main"

UPDATE_URL = UPDATE_BASIS + kanal_lesen() + "/"

def _sprache():
    """Sprache des Launchers: gespeicherte Wahl, sonst die Windows-Sprache (Deutsch oder Englisch)."""
    l = config_lesen().get("lang")
    if l in ("de", "en"):
        return l
    try:
        import ctypes
        if os.name == "nt" and (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x07:
            return "de"
    except Exception:
        pass
    return "en"

SPRACHE = None

def TR(de, en):
    """Gibt den deutschen oder englischen Text zurueck."""
    global SPRACHE
    if SPRACHE is None:
        SPRACHE = _sprache()
    return en if SPRACHE == "en" else de

def config_schreiben(d):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass

def laeuft():
    """True, wenn auf dem Port schon der RoK-Tracker antwortet."""
    try:
        with urllib.request.urlopen(URL + "api/status", timeout=1.5) as r:
            return "folder_ok" in json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False

def warten(sek, soll_laufen):
    ende = time.time() + sek
    while time.time() < ende:
        if laeuft() == soll_laufen:
            return True
        time.sleep(0.25)
    return laeuft() == soll_laufen

def beenden():
    """Beendet eine laufende Version. Neue Versionen per Anfrage, aeltere ueber den Port."""
    if not laeuft():
        return True
    log(TR("laufende Version wird beendet", "stopping the running version"))
    try:
        req = urllib.request.Request(URL + "api/quit", data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
    if warten(4, False):
        return True
    if os.name == "nt":
        try:
            kw = dict(capture_output=True, text=True, creationflags=0x08000000)
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], **kw).stdout
            pids = {l.split()[-1] for l in out.splitlines() if ":%d " % PORT in l and "LISTENING" in l}
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/F"], **kw)
        except Exception as e:
            log(TR("Beenden ueber Port fehlgeschlagen: %s", "Stopping via port failed: %s") % e)
    return warten(4, False)

def python_exe(mit_fenster=False):
    exe = sys.executable
    if os.name == "nt":
        ordner = os.path.dirname(exe)
        wunsch = os.path.join(ordner, "python.exe" if mit_fenster else "pythonw.exe")
        if os.path.exists(wunsch):
            return wunsch
    return exe

def tracker_starten():
    """Startet rok_barbs.py als eigenen Prozess (laeuft weiter, auch wenn der Launcher geschlossen wird)."""
    if laeuft():
        return True, ""
    os.makedirs(DATA, exist_ok=True)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        fehlerlog = open(TRACKER_LOG, "ab")
        fehlerlog.write(("\n--- %s ---\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
        fehlerlog.flush()
        p = subprocess.Popen([python_exe(), APP, "--no-browser"] + ARGS, cwd=HIER, stdin=subprocess.DEVNULL,
                             stdout=fehlerlog, stderr=fehlerlog, close_fds=True, **kw)
    except Exception as e:
        log("Start fehlgeschlagen: %s" % e)
        return False, str(e)
    ende = time.time() + 45
    while time.time() < ende:
        if laeuft():
            return True, ""
        if p.poll() is not None:
            break
        time.sleep(0.3)
    return False, TR("Das Programm hat sich beendet oder antwortet nicht. Siehe ", "The program has stopped or is not responding. See ") + TRACKER_LOG

def app_browser():
    """Pfad zu Edge oder Chrome (Windows) fuer das App-Fenster ohne Adressleiste, sonst None."""
    if os.name != "nt":
        return None
    teile = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for t in teile:
            if t and os.path.isfile(os.path.join(t, rel)):
                return os.path.join(t, rel)
    return None

def einmal_browser():
    try:
        c = config_lesen()
        if not c.get("browser_gewaehlt"):
            c["app_fenster"] = False
            c["browser_gewaehlt"] = True
            config_schreiben(c)
    except Exception:
        pass

def browser_oeffnen(ziel="", seite=""):
    if not seite and not ziel and kanal_lesen() == "v2":
        seite = "v2"                                   # Kanal v2: Start direkt auf der neuen Analyse-Seite
    url = URL + seite + (("#" + ziel) if ziel else "")
    if config_lesen().get("app_fenster", False):
        exe = app_browser()
        if exe:
            try:
                subprocess.Popen([exe, "--app=" + url], close_fds=True)
                return
            except Exception as e:
                log("App window failed: %s" % e)
    webbrowser.open(url)

# ---------------------------------------------------------------- Updates
def installierte_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()[:40] or None
    except Exception:
        return None

def neueste_version():
    """Fragt GitHub nach dem neuesten Stand. Diese Abfrage wird nicht zwischengespeichert, die Rohdateien schon (bis ca. 5 Min)."""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/?", UPDATE_URL)
    if not m:
        return None, None
    try:
        req = urllib.request.Request("https://api.github.com/repos/%s/%s/commits/%s" % m.groups(),
                                     headers={"User-Agent": "rok-tracker-launcher", "Accept": "application/vnd.github.sha"})
        with urllib.request.urlopen(req, timeout=6) as r:
            sha = r.read().decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha, "https://raw.githubusercontent.com/%s/%s/%s/" % (m.group(1), m.group(2), sha)
    except Exception as e:
        log("Versionsabfrage fehlgeschlagen: %s" % e)
    return None, None

def holen(name, basis=None):
    url = (basis or UPDATE_URL).rstrip("/") + "/" + name + "?t=" + str(int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": "rok-tracker-launcher", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read()

def aktualisieren(melden=lambda t: None):
    """Laedt neue Dateien. Gibt (Text, geaendert, launcher_geaendert, sha) zurueck."""
    if not UPDATE_URL:
        return TR("Kein Update-Server eingetragen", "No update server configured"), False, False, None
    melden(TR("Suche nach Updates ...", "Checking for updates ..."))
    sha, basis = neueste_version()
    geaendert = launcher = False
    for name in DATEIEN:
        try:
            melden(TR("Lade %s ...", "Downloading %s ...") % name)
            neu = holen(name, basis)
            if len(neu) < 2000:
                raise ValueError(TR("Antwort zu kurz", "Response too short"))
            compile(neu, name, "exec")                       # nur pruefen, nichts ausfuehren
            pfad = os.path.join(HIER, name)
            alt = open(pfad, "rb").read() if os.path.exists(pfad) else b""
            if hashlib.sha256(alt).digest() == hashlib.sha256(neu).digest():
                continue
            if alt:
                with open(pfad + ".bak", "wb") as f:
                    f.write(alt)
            with open(pfad + ".neu", "wb") as f:
                f.write(neu)
            os.replace(pfad + ".neu", pfad)
            log(TR("aktualisiert: %s (%s)", "updated: %s (%s)") % (name, sha[:7] if sha else TR("Zwischenspeicher", "cache")))
            geaendert = True
            launcher = launcher or name == os.path.basename(__file__)
        except Exception as e:
            log("Update %s uebersprungen: %s" % (name, e))
    if sha is None:
        return TR("GitHub nicht erreichbar, es kann noch die alte Version sein", "GitHub is not reachable, this may still be the old version"), geaendert, launcher, None
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(sha)
    except Exception:
        pass
    return (TR("Neue Version geladen (%s)", "New version downloaded (%s)") if geaendert else TR("Bereits aktuell (%s)", "Already up to date (%s)")) % sha[:7], geaendert, launcher, sha

def launcher_neu_starten():
    """Startet den (soeben aktualisierten) Launcher neu und beendet diesen."""
    try:                                                     # Schutz gegen Endlosschleife: nur kurz nach einem Neustart
        if time.time() - float(os.environ.get("ROK_RELAUNCHED", "0")) < 30:
            return False
    except ValueError:
        pass
    try:
        env = dict(os.environ, ROK_RELAUNCHED=str(time.time()))
        subprocess.Popen([python_exe(), os.path.abspath(__file__)] + ARGS + (["--autostart"] if AUTOSTART else []), cwd=HIER, env=env, close_fds=True,
                         creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)
        return True
    except Exception as e:
        log(TR("Neustart des Launchers fehlgeschlagen: %s", "Restarting the launcher failed: %s") % e)
        return False

def diagnose_starten():
    pfad = os.path.join(HIER, "rok_diagnose.py")
    if not os.path.exists(pfad):
        return False, TR("rok_diagnose.py fehlt. Erst auf Update pruefen.", "rok_diagnose.py is missing. Check for updates first.")
    kw = {"creationflags": 0x00000010} if os.name == "nt" else {}          # CREATE_NEW_CONSOLE
    subprocess.Popen([python_exe(True), pfad], cwd=HIER, **kw)
    return True, TR("Diagnose gestartet. Das Ergebnis liegt als diagnose.txt auf dem Desktop.", "Diagnostics started. The result is saved as diagnose.txt on the desktop.")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "RoKTracker"

def autostart_befehl():
    return '"%s" "%s" --autostart' % (python_exe(), os.path.abspath(__file__))

def autostart_aktiv():
    """True, wenn der Launcher mit Windows startet (Eintrag im Benutzer-Autostart, kein Administrator noetig)."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
    except Exception:
        return False

def autostart_setzen(an):
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if an:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_befehl())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        log("Autostart aendern fehlgeschlagen: %s" % e)
        return False

def ordner_oeffnen(pfad):
    try:
        os.makedirs(pfad, exist_ok=True)
        if os.name == "nt":
            os.startfile(pfad)                                   # noqa
        else:
            subprocess.Popen(["xdg-open", pfad])
    except Exception as e:
        log(TR("Ordner oeffnen fehlgeschlagen: %s", "Opening the folder failed: %s") % e)


# ---------------------------------------------------------------- Symbol und Infobereich (Taskleiste)
def symbol_png(g=128):
    def hx(c): return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    def mix(a, b, t): return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
    cl = lambda v: 0.0 if v < 0 else 1.0 if v > 1 else v
    stops = [hx(c) for c in ("#3b82f6", "#22d3ee", "#34d399", "#fbbf24", "#f472b6", "#8b5cf6")]
    bg, weiss = hx("#0b0b0c"), hx("#f2f2f2")
    P = [(.62, 0), (.14, .57), (.45, .57), (.34, 1), (.86, .38), (.55, .38)]
    bw, bh = g * .30, g * .40
    ox, oy = g / 2 - bw / 2, g / 2 - bh / 2
    P = [(ox + x * bw, oy + y * bh) for x, y in P]
    def innen(px, py):
        c = False
        for k in range(len(P)):
            x1, y1 = P[k]; x2, y2 = P[(k + 1) % len(P)]
            if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                c = not c
        return c
    R, ri, r = g / 2 - 14, g / 2 - 14 - g * .075, g * .22
    rows = []
    for y in range(g):
        row = bytearray([0])
        for x in range(g):
            px, py = x + .5, y + .5
            qx, qy = abs(px - g / 2) - (g / 2 - r - 1), abs(py - g / 2) - (g / 2 - r - 1)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
            a = cl(.5 - d)
            col = bg
            dist = math.hypot(px - g / 2, py - g / 2)
            cov = cl(R + .5 - dist) * cl(dist - ri + .5)
            if cov > 0:
                t = ((math.degrees(math.atan2(py - g / 2, px - g / 2)) + 360) % 360) / 360 * len(stops)
                i = int(t) % len(stops)
                col = mix(col, mix(stops[i], stops[(i + 1) % len(stops)], t - int(t)), cov)
            if ox - 1 <= px <= ox + bw + 1 and oy - 1 <= py <= oy + bh + 1:
                n = sum(innen(px - .5 + (i + .5) / 3, py - .5 + (j + .5) / 3) for i in range(3) for j in range(3))
                col = mix(col, weiss, n / 9)
            row += bytes((int(col[0] + .5), int(col[1] + .5), int(col[2] + .5), int(a * 255 + .5)))
        rows.append(bytes(row))
    raw = b"".join(rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", g, g, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

def symbol_schreiben(pfad):
    png = symbol_png(128)
    with open(pfad, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22) + png)


ICO = os.path.join(DATA, "rok.ico")

def symbol_sichern():
    """Legt das Programmsymbol an, falls es fehlt. Gibt den Pfad zurueck oder None."""
    try:
        if not os.path.exists(ICO):
            os.makedirs(DATA, exist_ok=True)
            symbol_schreiben(ICO)
        return ICO
    except Exception as e:
        log(TR("Symbol konnte nicht erstellt werden: %s", "Could not create the icon: %s") % e)
        return None

class TrayIcon:
    """Symbol im Windows-Infobereich (neben der Uhr). Linksklick: Fenster zeigen, Rechtsklick: Menue.
    Laeuft in einem eigenen Thread; Aktionen werden ueber die uebergebenen Funktionen an das Fenster gegeben."""
    WM_TRAY = 0x0400 + 20
    def __init__(self, ico, tip, auf_klick, auf_menue):
        self.ok, self.klick, self.menue, self.ico, self.tip = False, auf_klick, auf_menue, ico, tip
        self.hwnd = self.nid = None
        self.bereit = threading.Event()
        threading.Thread(target=self._lauf, daemon=True).start()
        self.bereit.wait(4)

    def _lauf(self):
        try:
            import ctypes
            from ctypes import wintypes as wt
            u, sh, k = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
            class WNDCLASSW(ctypes.Structure):
                _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                            ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                            ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]
            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                            ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                            ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                            ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]
            u.DefWindowProcW.argtypes, u.DefWindowProcW.restype = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], LRESULT
            u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
            u.CreateWindowExW.restype = wt.HWND
            u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
            u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
            u.CreatePopupMenu.restype = wt.HMENU
            u.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
            u.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
            u.DestroyMenu.argtypes = [wt.HMENU]
            u.SetForegroundWindow.argtypes = [wt.HWND]
            u.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
            u.LoadImageW.restype = wt.HANDLE
            u.LoadIconW.argtypes, u.LoadIconW.restype = [wt.HINSTANCE, ctypes.c_void_p], wt.HICON
            u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [wt.LPCWSTR], wt.HMODULE
            sh.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

            def wnd(hwnd, msg, wp, lp):
                if msg == self.WM_TRAY:
                    ev = lp & 0xFFFF
                    if ev in (0x0202, 0x0203):                    # Linksklick / Doppelklick
                        self.klick()
                    elif ev == 0x0205:                            # Rechtsklick
                        self._menue(u, ctypes, wt, hwnd)
                    return 0
                return u.DefWindowProcW(hwnd, msg, wp, lp)
            self._wp = WNDPROC(wnd)                               # Verweis behalten, sonst raeumt Python die Funktion weg
            inst = k.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._wp, inst, "RoKTrackerTray"
            u.RegisterClassW(ctypes.byref(wc))
            self.hwnd = u.CreateWindowExW(0, "RoKTrackerTray", "RoK Tracker", 0, 0, 0, 0, 0, None, None, inst, None)
            if not self.hwnd:
                raise OSError("Could not create the window for the tray icon")
            icon = u.LoadImageW(None, self.ico, 1, 0, 0, 0x10 | 0x40) if self.ico and os.path.exists(self.ico) else None
            if not icon:
                icon = u.LoadIconW(None, ctypes.c_void_p(32512))
            nid = NOTIFYICONDATAW()
            nid.cbSize, nid.hWnd, nid.uID = ctypes.sizeof(NOTIFYICONDATAW), self.hwnd, 1
            nid.uFlags, nid.uCallbackMessage, nid.hIcon, nid.szTip = 0x1 | 0x2 | 0x4, self.WM_TRAY, icon, self.tip[:120]
            if not sh.Shell_NotifyIconW(0, ctypes.byref(nid)):    # NIM_ADD
                raise OSError("Could not add the icon to the notification area")
            self.nid, self._sh, self._ct = nid, sh, ctypes
            self.ok = True
            self.bereit.set()
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log("Tray icon not available: " + traceback.format_exc())
        finally:
            self.bereit.set()

    def _menue(self, u, ct, wt, hwnd):
        m = u.CreatePopupMenu()
        u.AppendMenuW(m, 0, 1, TR("Fenster zeigen", "Show window"))
        u.AppendMenuW(m, 0, 2, TR("Dashboard öffnen", "Open dashboard"))
        u.AppendMenuW(m, 0x800, 0, None)
        u.AppendMenuW(m, 0, 3, TR("Launcher schließen", "Close launcher"))
        pt = wt.POINT()
        u.GetCursorPos(ct.byref(pt))
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(m, 0x100 | 0x2, pt.x, pt.y, 0, hwnd, None)      # TPM_RETURNCMD | TPM_RIGHTBUTTON
        u.DestroyMenu(m)
        if cmd:
            self.menue(cmd)

    def _aendern(self, flags, tip=None, titel="", text=""):
        if not self.ok:
            return
        try:
            n = self.nid
            n.uFlags = flags
            if tip is not None:
                n.szTip = tip[:120]
            n.szInfoTitle, n.szInfo, n.dwInfoFlags = titel[:60], text[:240], 1
            self._sh.Shell_NotifyIconW(1, self._ct.byref(n))                      # NIM_MODIFY
            n.uFlags = 0x1 | 0x2 | 0x4
        except Exception:
            log("Symbol-Aenderung fehlgeschlagen: " + traceback.format_exc())

    def tooltip(self, tip):
        self._aendern(0x1 | 0x2 | 0x4, tip=tip)

    def hinweis(self, titel, text):
        """Sprechblase neben der Uhr."""
        self._aendern(0x1 | 0x2 | 0x10, titel=titel, text=text)

    def entfernen(self):
        if self.ok:
            try:
                self._sh.Shell_NotifyIconW(2, self._ct.byref(self.nid))          # NIM_DELETE
            except Exception:
                pass
            self.ok = False


# ---------------------------------------------------------------- Fenster
C = dict(bg="#090d15", card="#0f1623", soft="#141d2e", ink="#e8edf6", mute="#8793a8", line="#1f2b42",
         acc="#3b82f6", acc2="#2f6fdb", good="#34d399", warn="#fbbf24", bad="#fb7185")
FONT = "Segoe UI"
CHAT_URL = "https://claude.ai/code/session_01LFjMW45VQY2gAnMtkdR1Uk"     # kann in launcher.json mit "chat_url" ueberschrieben werden

# Kacheln: (Symbol, Titel, Beschreibung, [(Knopftext, Aktion), ...]). Aktion None = noch nicht verfuegbar.
MODULE = [
    ("\u2694", TR("Barbaren- und Beute-Tracker", "Barbarian & loot tracker"), TR("Berichte, Festungen, Spielerkaempfe und Ark of Osiris", "Reports, forts, PvP battles and Ark of Osiris"), [(TR("Oeffnen", "Open"), "dashboard")]),
    ("\U0001F464", TR("Profil-Tracker", "Profile tracker"), TR("Macht, Kill Points und Verlauf aus deinen Profil-Screenshots", "Power, kill points and history from your profile screenshots"), [(TR("Oeffnen", "Open"), "profil")]),
    ("\U0001F6E1", TR("Allianz-Tracker", "Alliance tracker"), TR("Member-Rangliste: Power, Kills, Tech, Bau, Hilfen, Festungen", "Member ranking: power, kills, tech, building, helps, forts"),
     [(TR("Oeffnen", "Open"), "allianz"), (TR("Daten einfuegen", "Import data"), "allianz_import"), (TR("Bilder hinzufuegen", "Add images"), "allianz_bilder")]),
    ("\U0001F50D", TR("Diagnose", "Diagnostics"), TR("Prueft den mailcache und schreibt diagnose.txt auf den Desktop", "Checks the mailcache and writes diagnose.txt to the desktop"), [(TR("Starten", "Start"), "diagnose")]),
]
BILDER = os.path.join(DATA, "allianz", "bilder")

def json_einspielen(pfade):
    """Schickt JSON-Dateien (Allianz-Ranglisten) an den laufenden Tracker. Gibt (Zeilen, Fehlertext) zurueck."""
    n, fehler = 0, ""
    for pfad in pfade:
        try:
            with open(pfad, "rb") as f:
                daten = f.read()
            req = urllib.request.Request(URL + "api/alliance", data=daten, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                antwort = json.loads(r.read().decode("utf-8", "replace"))
            if antwort.get("ok"):
                n += antwort.get("n", 0)
            else:
                fehler = "%s: %s" % (os.path.basename(pfad), antwort.get("error"))
        except Exception as e:
            fehler = "%s: %s" % (os.path.basename(pfad), e)
    return n, fehler

def bilder_sammeln(pfade):
    """Kopiert Bilder in einen Ordner pro Tag. Gibt (Anzahl, Zielordner) zurueck."""
    import shutil
    ziel = os.path.join(BILDER, time.strftime("%Y-%m-%d"))
    os.makedirs(ziel, exist_ok=True)
    n = 0
    for pfad in pfade:
        name = os.path.basename(pfad)
        basis, ext = os.path.splitext(name)
        ziel_datei, i = os.path.join(ziel, name), 1
        while os.path.exists(ziel_datei):
            ziel_datei = os.path.join(ziel, "%s_%d%s" % (basis, i, ext))
            i += 1
        shutil.copy2(pfad, ziel_datei)
        n += 1
    return n, ziel

def mit_fenster():
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import filedialog, messagebox

    # Farben im Stil eines dunklen Spiele-Launchers: fast schwarz, feines Raster, graue Flaechen, kaum Farbe
    C.update(bg="#0b0b0c", grid="#141416", side="#121314", card="#17181a", soft="#232427", soft2="#2f3135",
             ink="#f2f2f2", mute="#8b8d93", line="#33353a", acc="#e6e6e8", acc2="#c4c4c8")

    root = tk.Tk()
    root.title("RoK Tracker Launcher")
    root.configure(bg=C["bg"])
    root.resizable(False, False)
    W, H = 1000, 650
    root.geometry("%dx%d+%d+%d" % (W, H, (root.winfo_screenwidth() - W) // 2, max(20, (root.winfo_screenheight() - H) // 3)))
    fams = set(tkfont.families(root))
    PIX = next((f for f in ("Press Start 2P", "Silkscreen", "Pixelify Sans", "VT323", "Consolas", "Lucida Console", "Courier New") if f in fams), "Courier")

    def rrect(cv, x1, y1, x2, y2, r, **kw):
        """Rechteck mit runden Ecken auf einer Zeichenflaeche."""
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    import math

    # Weiche Formen: Kanten werden pixelgenau mit Glaettung berechnet (Tk selbst zeichnet nur eckige Kanten)
    def hx(c):
        return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    def mixc(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
    def tohex(c):
        return "#%02x%02x%02x" % (int(c[0] + .5), int(c[1] + .5), int(c[2] + .5))
    def cl(v):
        return 0.0 if v < 0 else 1.0 if v > 1 else v
    def sdf(px, py, w, h, r):
        qx, qy = abs(px - w / 2) - (w / 2 - r), abs(py - h / 2) - (h / 2 - r)
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    _bilder = {}
    def bild(w, h, pix):
        im = tk.PhotoImage(master=root, width=w, height=h)
        im.put(" ".join("{" + " ".join(tohex(pix(x, y)) for x in range(w)) + "}" for y in range(h)))
        return im
    def rr_img(w, h, r, fill, outline, bg, bw=1.3, lip=0, lipc=None):
        key = ("rr", w, h, r, fill, outline, bg, bw, lip, lipc)
        if key not in _bilder:
            f, b = hx(fill), hx(bg)
            o = hx(outline) if outline else f
            lc = hx(lipc) if lipc else b
            hh = h - lip
            def pix(x, y):
                col = b
                if lip:
                    col = mixc(col, lc, cl(.5 - sdf(x + .5, y + .5 - lip, w, hh, r)))
                d = sdf(x + .5, y + .5, w, hh, r)
                col = mixc(col, o, cl(.5 - d))
                return mixc(col, f, cl(.5 - (d + bw)))
            _bilder[key] = bild(w, h, pix)
        return _bilder[key]
    def ring_img(size, dicke, stops, bg):
        key = ("ring", size, dicke, tuple(stops), bg)
        if key not in _bilder:
            b, cs = hx(bg), [hx(c) for c in stops]
            mid = size / 2
            R, ri, n = size / 2 - 1.5, size / 2 - 1.5 - dicke, len(cs)
            def pix(x, y):
                dx, dy = x + .5 - mid, y + .5 - mid
                dist = math.hypot(dx, dy)
                cov = cl(R + .5 - dist) * cl(dist - ri + .5)
                if cov <= 0:
                    return b
                t = ((math.degrees(math.atan2(dy, dx)) + 360) % 360) / 360 * n
                i = int(t) % n
                return mixc(b, mixc(cs[i], cs[(i + 1) % n], t - int(t)), cov)
            _bilder[key] = bild(size, size, pix)
        return _bilder[key]
    def blitz_img(w, h, farbe, bg):
        key = ("blitz", w, h, farbe, bg)
        if key not in _bilder:
            P = [(.62, 0), (.14, .57), (.45, .57), (.34, 1), (.86, .38), (.55, .38)]
            P = [(x * w, y * h) for x, y in P]
            b, f = hx(bg), hx(farbe)
            def innen(px, py):
                c = False
                for k in range(len(P)):
                    x1, y1 = P[k]
                    x2, y2 = P[(k + 1) % len(P)]
                    if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                        c = not c
                return c
            def pix(x, y):
                n = sum(innen(x + (i + .5) / 3, y + (j + .5) / 3) for i in range(3) for j in range(3))
                return mixc(b, f, n / 9)
            _bilder[key] = bild(w, h, pix)
        return _bilder[key]

    class Knopf(tk.Canvas):
        """Dunkler Knopf mit weichen Ecken. primaer = gross mit unterer Kante (wie der Start-Knopf), sonst flach."""
        def __init__(self, parent, text, cmd, primaer=False, klein=False, w=None, h=None, sub=None, bg=None):
            size = 8 if klein else (13 if primaer else 9)
            fnt = tkfont.Font(family=PIX, size=size, weight="bold")
            w = w or fnt.measure(text) + (28 if klein else 46)
            h = h or fnt.metrics("linespace") + (16 if klein else 28)
            self.bgc = bg or parent.cget("bg")
            tk.Canvas.__init__(self, parent, width=w, height=h, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
            self.w, self.h, self.cmd, self.aktiv, self.primaer = w, h, cmd, True, primaer
            self.lip = 6 if primaer else 0
            self.radius = 22 if primaer else min(h // 2, 18)
            self.rand = "#5b5e64" if primaer else C["line"]
            self.farbe, self.hover, self.textfarbe = C["soft"], C["soft2"], C["ink"]
            self.bgimg = self.create_image(0, 0, anchor="nw", image=self._bild(self.farbe))
            ym = (h - self.lip) / 2
            if sub:
                self.txt = self.create_text(w / 2, ym - 9, text=text, fill=self.textfarbe, font=(PIX, size, "bold"))
                self.create_text(w / 2, ym + 16, text=sub, fill=C["mute"], font=(FONT, 8))
            else:
                self.txt = self.create_text(w / 2, ym, text=text, fill=self.textfarbe, font=(PIX, size, "bold"))
            self.bind("<Button-1>", lambda e: self.cmd() if self.aktiv else None)
            self.bind("<Enter>", lambda e: self.itemconfigure(self.bgimg, image=self._bild(self.hover if self.aktiv else self.farbe)))
            self.bind("<Leave>", lambda e: self.itemconfigure(self.bgimg, image=self._bild(self.farbe)))
        def _bild(self, fill):
            return rr_img(self.w, self.h, self.radius, fill, self.rand, self.bgc, 1.3, self.lip, "#3c3e43" if self.primaer else None)
        def setze(self, aktiv=True, farbe=None, textfarbe=None):
            self.aktiv = aktiv
            if farbe:
                self.farbe = farbe
            self.textfarbe = textfarbe or C["ink"]
            self.itemconfigure(self.bgimg, image=self._bild(self.farbe))
            self.itemconfigure(self.txt, fill=self.textfarbe if aktiv else C["mute"])
            self.configure(cursor="hand2" if aktiv else "arrow")

    class Nav(tk.Canvas):
        """Eintrag der linken Leiste: grosses Symbol mit kleiner Beschriftung darunter."""
        def __init__(self, parent, icon, label, cmd):
            tk.Canvas.__init__(self, parent, width=88, height=80, bg=C["side"], highlightthickness=0, bd=0, cursor="hand2")
            self.cmd = cmd
            self.bgimg = self.create_image(8, 4, anchor="nw", image=rr_img(72, 72, 20, C["soft"], "#5b5e64", C["side"]), state="hidden")
            self.ic = self.create_text(44, 33, text=icon, fill=C["mute"], font=("Segoe UI Symbol", 20))
            self.tx = self.create_text(44, 61, text=label, fill=C["mute"], font=(PIX, 7, "bold"))
            self.on = False
            self.bind("<Button-1>", lambda e: self.cmd())
            self.bind("<Enter>", lambda e: self._f(True))
            self.bind("<Leave>", lambda e: self._f(False))
        def _f(self, hover):
            if not self.on:
                self.itemconfigure(self.ic, fill=C["ink"] if hover else C["mute"])
                self.itemconfigure(self.tx, fill=C["ink"] if hover else C["mute"])
        def setze(self, on):
            self.on = on
            self.itemconfigure(self.bgimg, state="normal" if on else "hidden")
            self.itemconfigure(self.ic, fill=C["ink"] if on else C["mute"])
            self.itemconfigure(self.tx, fill=C["ink"] if on else C["mute"])

    class Balken(tk.Canvas):
        """Duenner Fortschrittsbalken (laeuft hin und her, solange gearbeitet wird)."""
        def __init__(self, parent):
            tk.Canvas.__init__(self, parent, height=6, width=10, bg=C["bg"], highlightthickness=0, bd=0)
            self.an, self.pos, self.richtung = False, 0.0, 1
            self.bind("<Configure>", lambda e: self._zeichne())
        def _zeichne(self):
            w = self.winfo_width()
            self.delete("all")
            rrect(self, 0, 0, w, 6, 3, fill=C["soft"], outline="")
            if self.an:
                seg = w * 0.28
                x = self.pos * (w - seg)
                rrect(self, x, 0, x + seg, 6, 3, fill=C["acc"], outline="")
        def start(self, *a):
            if not self.an:
                self.an = True
                self._tick()
        def _tick(self):
            if not self.an:
                return
            self.pos += 0.035 * self.richtung
            if self.pos >= 1 or self.pos <= 0:
                self.richtung = -self.richtung
                self.pos = min(1.0, max(0.0, self.pos))
            self._zeichne()
            self.after(30, self._tick)
        def stop(self):
            self.an, self.pos = False, 0.0
            self._zeichne()

    class Txt:
        """Kleine Huelle, damit Textfelder auf der Zeichenflaeche wie Labels angesprochen werden koennen."""
        def __init__(self, cv, item):
            self.cv, self.item = cv, item
        def configure(self, text=None, fg=None):
            kw = {}
            if text is not None:
                kw["text"] = text
            if fg is not None:
                kw["fill"] = fg
            self.cv.itemconfigure(self.item, **kw)

    ui_q = queue.Queue()
    def ui(fn):
        ui_q.put(fn)
    def pump():
        try:
            while True:
                ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            log("Window error: " + traceback.format_exc())
        root.after(100, pump)
    root.report_callback_exception = lambda *a: log("Window error: " + "".join(traceback.format_exception(*a)))

    einmal_browser()
    cfg = config_lesen()
    chat_url = cfg.get("chat_url") or CHAT_URL
    st = dict(busy=False, installiert=installierte_version(), neueste=None, laeuft=False, veraltet=False,
              auto=bool(cfg.get("autoupdate", True)), tray=bool(cfg.get("tray", True)))
    ico = symbol_sichern()
    if ico and os.name == "nt":
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    # ---------------- Aufbau: links die Leiste, rechts die Flaeche mit feinem Raster
    side = tk.Frame(root, bg=C["side"], width=96)
    side.pack(side="left", fill="y")
    side.pack_propagate(False)
    tk.Frame(root, bg=C["line"], width=1).pack(side="left", fill="y")
    main = tk.Canvas(root, bg=C["bg"], highlightthickness=0, bd=0)
    main.pack(side="left", fill="both", expand=True)

    def raster(e=None):
        main.delete("raster")
        w, h = main.winfo_width(), main.winfo_height()
        for x in range(0, w, 32):
            main.create_line(x, 0, x, h, fill=C["grid"], tags="raster")
        for y in range(0, h, 32):
            main.create_line(0, y, w, y, fill=C["grid"], tags="raster")
        main.tag_lower("raster")
    main.bind("<Configure>", raster)

    def T(seite, x, y, text, size=10, farbe=None, fett=False, anker="nw", schrift=None, **kw):
        return main.create_text(x, y, text=text, fill=farbe or C["ink"], anchor=anker,
                                font=(schrift or PIX, size, "bold" if fett else "normal"), tags=("p_" + seite,), **kw)
    def R(seite, x1, y1, x2, y2, r=22, fill=None, rand=None, bgc=None):
        im = rr_img(int(x2 - x1), int(y2 - y1), r, fill or C["card"], rand or C["line"], bgc or C["bg"])
        return main.create_image(x1, y1, image=im, anchor="nw", tags=("p_" + seite,))
    def Wf(seite, x, y, widget, anker="nw"):
        return main.create_window(x, y, window=widget, anchor=anker, tags=("p_" + seite,))

    # Kopfzeile (auf jeder Seite sichtbar)
    main.create_text(36, 26, text="ROK TRACKER", fill=C["ink"], anchor="nw", font=(PIX, 16, "bold"))
    main.create_text(37, 54, text="LAUNCHER", fill=C["mute"], anchor="nw", font=(PIX, 8, "bold"))
    main.create_image(520, 20, anchor="nw", image=rr_img(180, 40, 20, C["card"], C["line"], C["bg"]))
    punkt = main
    kreis = main.create_oval(536, 35, 546, 45, fill=C["mute"], outline="")
    t_tracker = Txt(main, main.create_text(554, 40, text=TR("PRÜFE ...", "CHECKING ..."), fill=C["ink"], anchor="w", font=(PIX, 8, "bold")))
    kopf_chat = Knopf(main, "CLAUDE CHAT", lambda: webbrowser.open(chat_url), klein=True)
    main.create_window(716, 40, window=kopf_chat, anchor="w")

    # Fuss (auf jeder Seite sichtbar)
    bar = Balken(main)
    main.create_window(36, 592, window=bar, anchor="nw", width=832)
    status = Txt(main, main.create_text(36, 608, text=TR("Bereit", "Ready"), fill=C["mute"], anchor="nw", font=(FONT, 9), width=832))

    def melden(text):
        ui(lambda: status.configure(text=text))

    def arbeit(fn, text):
        """Fuehrt fn in einem Thread aus, solange nichts anderes laeuft."""
        if st["busy"]:
            return
        st["busy"] = True
        def lauf():
            ui(lambda: (status.configure(text=text), bar.start(), sperren(True)))
            try:
                fn()
            except Exception:
                log("Error: " + traceback.format_exc())
                melden(TR("Fehler, Details in update.log", "Error, details in update.log"))
            finally:
                st["busy"] = False
                ui(lambda: (bar.stop(), sperren(False)))
                pruefen(False)
        threading.Thread(target=lauf, daemon=True).start()

    def update_ausfuehren():
        text, geaendert, launcher, sha = aktualisieren(melden)
        if sha:
            st["installiert"], st["neueste"] = sha, sha
            if tray and tray.ok:
                tray.tooltip("RoK Tracker")
        melden(text)
        if geaendert and laeuft():
            st["veraltet"] = True
            melden(text + TR(". Zum Uebernehmen: Neu starten.", ". To apply it: restart."))
        if launcher and launcher_neu_starten():
            ui(lambda: (log(TR("Launcher aktualisiert, startet neu", "Launcher updated, restarting")), root.destroy()))

    def start_und_oeffnen(ziel="", seite="", oeffnen=True):
        melden(TR("Starte Tracker ...", "Starting tracker ..."))
        ok, fehler = tracker_starten()
        if ok and not oeffnen:
            melden(TR("Tracker laeuft neu. Offenes Fenster einfach aktualisieren (F5).", "Tracker is running again. Just refresh the open window (F5)."))
        elif ok:
            browser_oeffnen(ziel, seite)
            melden(TR("Tracker laeuft. Browser geoeffnet.", "Tracker is running. Browser opened."))
        else:
            melden(TR("Start fehlgeschlagen. ", "Start failed. ") + fehler)

    def neu_starten():
        melden(TR("Beende laufende Version ...", "Stopping the running version ..."))
        beenden()
        if st["auto"]:
            update_ausfuehren()
        st["veraltet"] = False
        start_und_oeffnen(oeffnen=False)         # Neustart: kein neues Fenster, das offene einfach mit F5 aktualisieren

    def tracker_beenden():
        melden(TR("Beende Tracker ...", "Stopping tracker ..."))
        ok = beenden()
        melden(TR("Tracker beendet.", "Tracker stopped.") if ok else TR("Konnte den Tracker nicht beenden.", "Could not stop the tracker."))

    def aktion(name):
        if name == "dashboard":
            arbeit(lambda: start_und_oeffnen(), TR("Oeffne Dashboard ...", "Opening dashboard ..."))
        elif name == "profil":
            arbeit(lambda: start_und_oeffnen("profil"), TR("Oeffne Profil ...", "Opening profile ..."))
        elif name == "allianz":
            arbeit(lambda: start_und_oeffnen(seite="allianz"), TR("Oeffne Allianz-Tracker ...", "Opening alliance tracker ..."))
        elif name == "allianz_import":
            pfade = filedialog.askopenfilenames(title=TR("Allianz-Datei (JSON) waehlen", "Choose alliance file (JSON)"), filetypes=[(TR("JSON-Datei", "JSON file"), "*.json"), (TR("Alle Dateien", "All files"), "*.*")])
            if pfade:
                def imp():
                    melden(TR("Starte Tracker ...", "Starting tracker ..."))
                    ok, fehler = tracker_starten()
                    if not ok:
                        return melden(TR("Start fehlgeschlagen. ", "Start failed. ") + fehler)
                    n, f = json_einspielen(pfade)
                    melden((TR("%d Eintraege uebernommen.", "%d entries imported.") % n) + (TR(" Fehler: ", " Error: ") + f if f else TR(" Im Dashboard unter Allianz ansehen.", " See them in the dashboard under Alliance.")))
                arbeit(imp, TR("Spiele Allianz-Daten ein ...", "Importing alliance data ..."))
        elif name == "allianz_bilder":
            pfade = filedialog.askopenfilenames(title=TR("Allianz-Screenshots waehlen", "Choose alliance screenshots"),
                                                filetypes=[(TR("Bilder", "Images"), "*.png *.jpg *.jpeg *.webp"), (TR("Alle Dateien", "All files"), "*.*")])
            if pfade:
                def bilder():
                    n, ziel = bilder_sammeln(pfade)
                    melden(TR("%d Bilder gesammelt in %s", "%d images collected in %s") % (n, ziel))
                    def fragen():
                        if messagebox.askyesno(TR("Bilder gesammelt", "Images collected"),
                                               TR("%d Bilder wurden gesammelt.\n\nDie Auswertung macht Claude im Chat. Chat und Ordner jetzt oeffnen, "
                                                  "um die Bilder dort hochzuladen?", "%d images were collected.\n\nClaude analyzes them in the chat. Open the chat and the folder now "
                                                  "to upload the images there?") % n):
                            ordner_oeffnen(ziel)
                            webbrowser.open(chat_url)
                    ui(fragen)
                arbeit(bilder, TR("Kopiere Bilder ...", "Copying images ..."))
        elif name == "diagnose":
            def d():
                ok, t = diagnose_starten()
                melden(t)
            arbeit(d, TR("Starte Diagnose ...", "Starting diagnostics ..."))


    # ---------------- Seite START
    cx = 296
    main.create_image(cx, 166, image=ring_img(156, 11, ("#3b82f6", "#22d3ee", "#34d399", "#fbbf24", "#f472b6", "#8b5cf6"), C["bg"]), tags=("p_play",))
    T("play", cx, 166, "RK", 22, fett=True, anker="center")
    T("play", cx, 268, "ROK TRACKER", 20, fett=True, anker="center")
    T("play", cx, 298, TR("BARBAREN, BEUTE UND PROFIL", "BARBARIANS, LOOT AND PROFILE"), 8, C["mute"], True, "center")
    b_start = Knopf(main, TR("DASHBOARD ÖFFNEN", "OPEN DASHBOARD"), lambda: aktion("dashboard"), primaer=True, w=340, h=88, sub=TR("Startet den Tracker und öffnet den Browser", "Starts the tracker and opens the browser"))
    Wf("play", cx - 44, 392, b_start, "center")
    menue = tk.Menu(root, tearoff=0, bg=C["card"], fg=C["ink"], activebackground=C["soft2"], activeforeground=C["ink"], bd=0, font=(FONT, 10))
    b_menue = Knopf(main, "▾", lambda: menue.tk_popup(b_menue.winfo_rootx(), b_menue.winfo_rooty() + b_menue.winfo_height()), primaer=True, w=76, h=88)
    Wf("play", cx + 172, 392, b_menue, "center")

    # rechts: Tools als Kacheln
    x0, x1 = 556, 868
    T("play", x0 + 2, 108, "TOOLS", 11, fett=True)
    main.create_line(x0, 134, x1, 134, fill=C["line"], tags=("p_play",))
    y = 150
    for symbol, name, beschr, knoepfe in MODULE[1:]:
        zeilen = (len(knoepfe) + 1) // 2
        hoehe = 84 + zeilen * 34
        R("play", x0, y, x1, y + hoehe, 16)
        R("play", x0 + 14, y + 14, x0 + 58, y + 58, 14, C["soft"], C["soft"], C["card"])
        T("play", x0 + 36, y + 36, symbol, 15, anker="center", schrift="Segoe UI Emoji")
        T("play", x0 + 70, y + 16, name.upper(), 9, fett=True)
        T("play", x0 + 70, y + 36, beschr, 8, C["mute"], schrift=FONT, width=x1 - x0 - 84)
        for zeile in range(0, len(knoepfe), 2):
            xx = x0 + 14
            for text, akt in knoepfe[zeile:zeile + 2]:
                kn = Knopf(main, text.upper(), (lambda a=akt: aktion(a)) if akt else (lambda: None), klein=True, bg=C["card"])
                Wf("play", xx, y + 70 + (zeile // 2) * 34, kn)
                xx += int(kn.cget("width")) + 8
        y += hoehe + 12

    # ---------------- Seite UPDATE
    upd_karte = main.create_image(60, 112, anchor="nw", tags=("p_upd",))      # Bild folgt erst beim ersten Oeffnen der Seite
    T("upd", 92, 140, TR("INSTALLIERT", "INSTALLED"), 8, C["mute"], True)
    t_version = Txt(main, T("upd", 92, 162, "...", 16, fett=True))
    t_version2 = Txt(main, T("upd", 92, 196, "", 10, C["mute"], schrift=FONT))
    b_upd = Knopf(main, TR("UPDATE SUCHEN", "CHECK FOR UPDATES"), lambda: arbeit(update_ausfuehren, TR("Suche nach Updates ...", "Checking for updates ...")), bg=C["card"])
    Wf("upd", 92, 246, b_upd)
    b_neu = Knopf(main, TR("NEU STARTEN", "RESTART"), lambda: arbeit(neu_starten, TR("Starte neu ...", "Restarting ...")), bg=C["card"])
    Wf("upd", 92 + int(b_upd.cget("width")) + 10, 246, b_neu)
    b_stop = Knopf(main, TR("BEENDEN", "STOP"), lambda: arbeit(tracker_beenden, TR("Beende Tracker ...", "Stopping tracker ...")), bg=C["card"])
    Wf("upd", 92 + int(b_upd.cget("width")) + int(b_neu.cget("width")) + 20, 246, b_stop)
    alle = (b_start, b_neu, b_upd, b_stop)

    def sperren(an):
        for b in alle:
            b.setze(not an)
        if not an:
            update_da = st["veraltet"] or bool(st["neueste"] and st["installiert"] != st["neueste"])
            b_start.setze(True)
            b_upd.setze(True, C["warn"] if update_da else C["soft"], "#111111" if update_da else None)

    autoupdate = tk.BooleanVar(value=st["auto"])
    def auto_geaendert(*a):
        st["auto"] = bool(autoupdate.get())
        c = config_lesen()
        c["autoupdate"] = st["auto"]
        config_schreiben(c)
    autoupdate.trace_add("write", auto_geaendert)
    Wf("upd", 90, 310, tk.Checkbutton(main, text=TR("Beim Neustart automatisch nach Updates suchen", "Check for updates automatically on restart"), variable=autoupdate, bg=C["card"], fg=C["mute"],
                                      selectcolor=C["soft"], activebackground=C["card"], activeforeground=C["ink"], font=(FONT, 10),
                                      highlightthickness=0, bd=0))
    autowin = tk.BooleanVar(value=autostart_aktiv())
    def autowin_geaendert(*a):
        if not autostart_setzen(bool(autowin.get())):
            autowin.set(autostart_aktiv())
            melden(TR("Autostart konnte nicht geändert werden (nur unter Windows möglich).", "Could not change autostart (Windows only)."))
        else:
            melden(TR("Autostart aktiviert. Der Launcher startet mit Windows und öffnet den Tracker im Hintergrund.", "Autostart enabled. The launcher starts with Windows and runs the tracker in the background.") if autowin.get() else TR("Autostart deaktiviert.", "Autostart disabled."))
    autowin.trace_add("write", autowin_geaendert)
    Wf("upd", 90, 338, tk.Checkbutton(main, text=TR("Mit Windows starten (Tracker läuft dann im Hintergrund)", "Start with Windows (tracker then runs in the background)"), variable=autowin, bg=C["card"], fg=C["mute"],
                                      selectcolor=C["soft"], activebackground=C["card"], activeforeground=C["ink"], font=(FONT, 10),
                                      highlightthickness=0, bd=0, state="normal" if os.name == "nt" else "disabled"))
    traywahl = tk.BooleanVar(value=st["tray"])
    def tray_geaendert(*a):
        st["tray"] = bool(traywahl.get())
        c = config_lesen()
        c["tray"] = st["tray"]
        config_schreiben(c)
    traywahl.trace_add("write", tray_geaendert)
    appwahl = tk.BooleanVar(value=bool(cfg.get("app_fenster", False)))
    def app_geaendert(*a):
        c = config_lesen()
        c["app_fenster"] = bool(appwahl.get())
        config_schreiben(c)
    appwahl.trace_add("write", app_geaendert)
    Wf("upd", 90, 394, tk.Checkbutton(main, text=TR("Tracker als App-Fenster öffnen (ohne Adressleiste, Edge oder Chrome)", "Open the tracker as an app window (no address bar, Edge or Chrome)"), variable=appwahl, bg=C["card"], fg=C["mute"],
                                      selectcolor=C["soft"], activebackground=C["card"], activeforeground=C["ink"], font=(FONT, 10),
                                      highlightthickness=0, bd=0, state="normal" if os.name == "nt" else "disabled"))
    Wf("upd", 90, 366, tk.Checkbutton(main, text=TR("Beim Schließen (X) in den Infobereich neben der Uhr minimieren", "Minimize to the notification area next to the clock when closing (X)"), variable=traywahl, bg=C["card"], fg=C["mute"],
                                      selectcolor=C["soft"], activebackground=C["card"], activeforeground=C["ink"], font=(FONT, 10),
                                      highlightthickness=0, bd=0, state="normal" if os.name == "nt" else "disabled"))
    k_prot = Knopf(main, TR("PROTOKOLL", "LOG"), lambda: ordner_oeffnen(DATA) if not os.path.exists(LOG) else
                   (os.startfile(LOG) if os.name == "nt" else None), klein=True, bg=C["card"])
    Wf("upd", 92, 428, k_prot)
    Wf("upd", 92 + int(k_prot.cget("width")) + 10, 428, Knopf(main, TR("DATENORDNER", "DATA FOLDER"), lambda: ordner_oeffnen(DATA), klein=True, bg=C["card"]))

    menue.add_command(label=TR("Neu starten", "Restart"), command=lambda: arbeit(neu_starten, TR("Starte neu ...", "Restarting ...")))
    menue.add_command(label=TR("Nach Update suchen", "Check for updates"), command=lambda: arbeit(update_ausfuehren, TR("Suche nach Updates ...", "Checking for updates ...")))
    menue.add_separator()
    kanal_var = tk.StringVar(value=kanal_lesen())
    def kanal_wechseln():
        global UPDATE_URL
        k = kanal_var.get()
        c = config_lesen()
        c["kanal"] = k
        config_schreiben(c)
        UPDATE_URL = UPDATE_BASIS + k + "/"
        log("Version gewechselt: " + k)
        melden(TR("Wechsle Version: ", "Switching version: ") + KANAELE[k])
        update_ausfuehren()
        melden(TR("Starte neu ...", "Restarting ..."))
        beenden()
        st["veraltet"] = False
        start_und_oeffnen(oeffnen=False)
    vmenue = tk.Menu(menue, tearoff=0, bg=C["card"], fg=C["ink"], activebackground=C["soft2"], activeforeground=C["ink"], bd=0, font=(FONT, 10), selectcolor=C["ink"])
    for k, name in (("classic", TR("Classic (Backup vom 16.11.2026)", "Classic (backup of 16 Nov 2026)")), ("main", TR("Aktuell (main)", "Current (main)")), ("v2", TR("Neu (v2)", "New (v2)"))):
        vmenue.add_radiobutton(label=name, variable=kanal_var, value=k, command=lambda: arbeit(kanal_wechseln, TR("Wechsle Version ...", "Switching version ...")))
    menue.add_cascade(label=TR("Version wechseln", "Switch version"), menu=vmenue)
    menue.add_separator()
    menue.add_command(label=TR("Tracker beenden", "Stop tracker"), command=lambda: arbeit(tracker_beenden, TR("Beende Tracker ...", "Stopping tracker ...")))

    # ---------------- linke Leiste
    logo = tk.Canvas(side, width=96, height=84, bg=C["side"], highlightthickness=0, bd=0)
    logo.pack()
    logo.create_image(48, 44, image=blitz_img(34, 46, C["ink"], C["side"]))
    navs = {}
    def zeige_seite(name):
        if name == "upd" and not main.itemcget(upd_karte, "image"):
            main.itemconfigure(upd_karte, image=rr_img(784, 348, 24, C["card"], C["line"], C["bg"]))
        for sname in ("play", "upd"):
            main.itemconfigure("p_" + sname, state="normal" if sname == name else "hidden")
        for k, nv in navs.items():
            nv.setze(k == name)
    for key, icon, label, cmd in (("play", "▶", TR("START", "START"), lambda: zeige_seite("play")),
                                  ("upd", "⬇", "UPDATE", lambda: zeige_seite("upd")),
                                  ("chat", "💬", "CHAT", lambda: webbrowser.open(chat_url))):
        nv = Nav(side, icon, label, cmd)
        nv.pack(pady=2)
        navs[key] = nv
    Nav(side, "⚙", TR("DATEN", "DATA"), lambda: ordner_oeffnen(DATA)).pack(side="bottom", pady=(0, 10))
    def sprache_wechseln():
        """Speichert die andere Sprache und startet den Launcher neu, damit alle Texte neu aufgebaut werden."""
        c = config_lesen()
        c["lang"] = "de" if TR("de", "en") == "en" else "en"
        config_schreiben(c)
        try:
            subprocess.Popen([python_exe(), os.path.abspath(__file__)] + ARGS, cwd=HIER, close_fds=True,
                             creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)
        except Exception as e:
            melden("%s: %s" % (TR("Neustart fehlgeschlagen", "Restart failed"), e))
            return
        ganz_schliessen()
    Nav(side, "🌐", TR("ENGLISH", "DEUTSCH"), sprache_wechseln).pack(side="bottom", pady=2)
    zeige_seite("play")

    # --- Status regelmaessig aktualisieren (im Hintergrund, Anzeige im Fenster)
    def zeige():
        if st["laeuft"]:
            punkt.itemconfigure(kreis, fill=C["good"])
            t_tracker.configure(text=TR("LÄUFT · PORT %d", "RUNNING · PORT %d") % PORT)
        else:
            punkt.itemconfigure(kreis, fill=C["mute"])
            t_tracker.configure(text=TR("GESTOPPT", "STOPPED"))
        inst = (st["installiert"] or TR("unbekannt", "unknown"))[:7]
        neu = (st["neueste"] or "")[:7]
        t_version.configure(text=TR("Installiert: ", "Installed: ") + inst)
        if st["veraltet"]:
            t_version2.configure(text=TR("Update geladen, Neustart nötig", "Update downloaded, restart required"), fg=C["warn"])
        elif st["neueste"] and st["installiert"] != st["neueste"]:
            t_version2.configure(text=TR("Update verfügbar (%s)", "Update available (%s)") % neu, fg=C["warn"])
        elif st["neueste"]:
            t_version2.configure(text=TR("Aktuell", "Up to date"), fg=C["good"])
        else:
            t_version2.configure(text=TR("GitHub nicht erreichbar", "GitHub not reachable"), fg=C["mute"])
        if not st["busy"]:
            sperren(False)

    def pruefen(mit_version=True):
        def lauf():
            st["laeuft"] = laeuft()
            if mit_version:
                sha, _ = neueste_version()
                if sha:
                    st["neueste"] = sha
                    if tray and tray.ok and st["installiert"] and sha != st["installiert"] and st.get("gemeldet") != sha:
                        st["gemeldet"] = sha
                        ui(lambda: (tray.tooltip(TR("RoK Tracker - Update verfügbar", "RoK Tracker - update available")),
                                    tray.hinweis("RoK Tracker", TR("Ein Update ist verfügbar. Klick auf das Symbol, dann auf Update.", "An update is available. Click the icon, then Update."))))
            ui(zeige)
        threading.Thread(target=lauf, daemon=True).start()

    def takt():
        if not st["busy"]:
            pruefen(False)
        root.after(3000, takt)

    def update_takt():
        """Fragt alle 3 Stunden bei GitHub nach, auch wenn das Fenster im Infobereich versteckt ist."""
        if not st["busy"]:
            pruefen(True)
        root.after(3 * 3600 * 1000, update_takt)
    root.after(3 * 3600 * 1000, update_takt)

    tray = None
    def zeigen():
        root.deiconify()
        root.lift()
        try:
            root.focus_force()
        except Exception:
            pass
    def ganz_schliessen():
        if tray:
            tray.entfernen()
        root.destroy()
    if os.name == "nt":
        menue_aktionen = {1: zeigen, 2: lambda: aktion("dashboard"), 3: ganz_schliessen}
        tray = TrayIcon(ico, "RoK Tracker", lambda: ui(zeigen), lambda c: ui(menue_aktionen.get(c, lambda: None)))
    def x_gedrueckt():
        if tray and tray.ok and st["tray"]:
            root.withdraw()                                   # bleibt als Symbol neben der Uhr erreichbar
        else:
            ganz_schliessen()
    root.protocol("WM_DELETE_WINDOW", x_gedrueckt)
    pump()
    pruefen(True)
    root.after(3000, takt)
    if AUTOSTART:
        if tray and tray.ok:
            root.withdraw()
        else:
            root.iconify()
        def leise():
            if st["auto"]:
                update_ausfuehren()
            ok, fehler = tracker_starten()
            melden(TR("Tracker läuft im Hintergrund.", "Tracker is running in the background.") if ok else TR("Start fehlgeschlagen. ", "Start failed. ") + fehler)
        root.after(800, lambda: arbeit(leise, TR("Starte Tracker ...", "Starting tracker ...")))
    elif st["auto"]:
        root.after(600, lambda: arbeit(update_ausfuehren, TR("Suche nach Updates ...", "Checking for updates ...")))
    root.mainloop()

def ohne_fenster():
    if not AUTOSTART:
        beenden()
    aktualisieren()
    ok, fehler = tracker_starten()
    if ok and not AUTOSTART:
        browser_oeffnen()
    else:
        log("Start failed: " + fehler)

if __name__ == "__main__":
    try:
        import tkinter  # noqa
        gui = True
    except Exception:
        gui = False
    if gui:
        try:
            mit_fenster()
        except Exception:
            log("Could not build the window: " + traceback.format_exc())
            ohne_fenster()
    else:
        ohne_fenster()
