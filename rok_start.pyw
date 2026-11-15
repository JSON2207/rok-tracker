# -*- coding: utf-8 -*-
"""RoK-Tracker Launcher: Fenster mit Status, Update, Start/Neustart/Beenden und Tool-Kacheln.
Doppelklick genuegt (kein Konsolenfenster). Lege diese Datei in denselben Ordner wie rok_barbs.py.
Ohne tkinter oder bei einem Fehler im Fenster startet der Tracker wie frueher direkt."""
import hashlib, json, os, queue, re, subprocess, sys, threading, time, traceback, urllib.request, webbrowser

UPDATE_URL = "https://raw.githubusercontent.com/JSON2207/rok-tracker/main/"
DATEIEN = ["rok_barbs.py", "rok_diagnose.py", "rok_start.pyw"]      # fehlt eine online, wird sie uebersprungen

HIER = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HIER, "rok_barbs.py")
DATA = os.path.join(os.environ.get("APPDATA", HIER), "RoK-Tracker")
LOG = os.path.join(DATA, "update.log")
TRACKER_LOG = os.path.join(DATA, "tracker.log")
VERSION_FILE = os.path.join(DATA, "version.txt")
CONFIG = os.path.join(DATA, "launcher.json")
ARGS = [a for a in sys.argv[1:]]
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
    log("laufende Version wird beendet")
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
            log("Beenden ueber Port fehlgeschlagen: %s" % e)
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
    return False, "Das Programm hat sich beendet oder antwortet nicht. Siehe " + TRACKER_LOG

def browser_oeffnen(ziel="", seite=""):
    webbrowser.open(URL + seite + (("#" + ziel) if ziel else ""))

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
        return "Kein Update-Server eingetragen", False, False, None
    melden("Suche nach Updates ...")
    sha, basis = neueste_version()
    geaendert = launcher = False
    for name in DATEIEN:
        try:
            melden("Lade %s ..." % name)
            neu = holen(name, basis)
            if len(neu) < 2000:
                raise ValueError("Antwort zu kurz")
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
            log("aktualisiert: %s (%s)" % (name, sha[:7] if sha else "Zwischenspeicher"))
            geaendert = True
            launcher = launcher or name == os.path.basename(__file__)
        except Exception as e:
            log("Update %s uebersprungen: %s" % (name, e))
    if sha is None:
        return "GitHub nicht erreichbar, es kann noch die alte Version sein", geaendert, launcher, None
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(sha)
    except Exception:
        pass
    return ("Neue Version geladen (%s)" if geaendert else "Bereits aktuell (%s)") % sha[:7], geaendert, launcher, sha

def launcher_neu_starten():
    """Startet den (soeben aktualisierten) Launcher neu und beendet diesen."""
    try:                                                     # Schutz gegen Endlosschleife: nur kurz nach einem Neustart
        if time.time() - float(os.environ.get("ROK_RELAUNCHED", "0")) < 30:
            return False
    except ValueError:
        pass
    try:
        env = dict(os.environ, ROK_RELAUNCHED=str(time.time()))
        subprocess.Popen([python_exe(), os.path.abspath(__file__)] + ARGS, cwd=HIER, env=env, close_fds=True,
                         creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)
        return True
    except Exception as e:
        log("Neustart des Launchers fehlgeschlagen: %s" % e)
        return False

def diagnose_starten():
    pfad = os.path.join(HIER, "rok_diagnose.py")
    if not os.path.exists(pfad):
        return False, "rok_diagnose.py fehlt. Erst auf Update pruefen."
    kw = {"creationflags": 0x00000010} if os.name == "nt" else {}          # CREATE_NEW_CONSOLE
    subprocess.Popen([python_exe(True), pfad], cwd=HIER, **kw)
    return True, "Diagnose gestartet. Das Ergebnis liegt als diagnose.txt auf dem Desktop."

def ordner_oeffnen(pfad):
    try:
        os.makedirs(pfad, exist_ok=True)
        if os.name == "nt":
            os.startfile(pfad)                                   # noqa
        else:
            subprocess.Popen(["xdg-open", pfad])
    except Exception as e:
        log("Ordner oeffnen fehlgeschlagen: %s" % e)

# ---------------------------------------------------------------- Fenster
C = dict(bg="#090d15", card="#0f1623", soft="#141d2e", ink="#e8edf6", mute="#8793a8", line="#1f2b42",
         acc="#3b82f6", acc2="#2f6fdb", good="#34d399", warn="#fbbf24", bad="#fb7185")
FONT = "Segoe UI"
CHAT_URL = "https://claude.ai/code/session_01LFjMW45VQY2gAnMtkdR1Uk"     # kann in launcher.json mit "chat_url" ueberschrieben werden

# Kacheln: (Symbol, Titel, Beschreibung, [(Knopftext, Aktion), ...]). Aktion None = noch nicht verfuegbar.
MODULE = [
    ("\u2694", "Barbaren- und Beute-Tracker", "Berichte, Festungen, Spielerkaempfe und Ark of Osiris", [("Oeffnen", "dashboard")]),
    ("\U0001F464", "Profil-Tracker", "Macht, Kill Points und Verlauf aus deinen Profil-Screenshots", [("Oeffnen", "profil")]),
    ("\U0001F6E1", "Allianz-Tracker", "Member-Rangliste: Power, Kills, Tech, Bau, Hilfen, Festungen",
     [("Oeffnen", "allianz"), ("Daten einfuegen", "allianz_import"), ("Bilder hinzufuegen", "allianz_bilder")]),
    ("\U0001F50D", "Diagnose", "Prueft den mailcache und schreibt diagnose.txt auf den Desktop", [("Starten", "diagnose")]),
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

    class Knopf(tk.Canvas):
        """Dunkler Knopf mit Rand. primaer = gross mit unterer Kante (wie der Start-Knopf), sonst flach."""
        def __init__(self, parent, text, cmd, primaer=False, klein=False, w=None, h=None, sub=None, bg=None):
            size = 8 if klein else (13 if primaer else 9)
            fnt = tkfont.Font(family=PIX, size=size, weight="bold")
            w = w or fnt.measure(text) + (26 if klein else 44)
            h = h or fnt.metrics("linespace") + (14 if klein else 26)
            tk.Canvas.__init__(self, parent, width=w, height=h, bg=bg or parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
            self.cmd, self.aktiv, self.primaer = cmd, True, primaer
            self.farbe, self.hover = (C["soft"], C["soft2"])
            self.textfarbe = C["ink"]
            lip = 5 if primaer else 0
            if lip:
                rrect(self, 0, lip, w, h, 16, fill="#44464b", outline="")
            self.form = rrect(self, 1, 1, w - 1, h - lip - 1, 14 if primaer else 10, fill=self.farbe,
                              outline="#5b5e64" if primaer else C["line"], width=2)
            ym = (h - lip) / 2
            if sub:
                self.txt = self.create_text(w / 2, ym - 9, text=text, fill=self.textfarbe, font=(PIX, size, "bold"))
                self.create_text(w / 2, ym + 16, text=sub, fill=C["mute"], font=(FONT, 8))
            else:
                self.txt = self.create_text(w / 2, ym, text=text, fill=self.textfarbe, font=(PIX, size, "bold"))
            self.bind("<Button-1>", lambda e: self.cmd() if self.aktiv else None)
            self.bind("<Enter>", lambda e: self.itemconfigure(self.form, fill=self.hover if self.aktiv else self.farbe))
            self.bind("<Leave>", lambda e: self.itemconfigure(self.form, fill=self.farbe))
        def setze(self, aktiv=True, farbe=None, textfarbe=None):
            self.aktiv = aktiv
            if farbe:
                self.farbe = farbe
            self.textfarbe = textfarbe or C["ink"]
            self.itemconfigure(self.form, fill=self.farbe)
            self.itemconfigure(self.txt, fill=self.textfarbe if aktiv else C["mute"])
            self.configure(cursor="hand2" if aktiv else "arrow")

    class Nav(tk.Canvas):
        """Eintrag der linken Leiste: grosses Symbol mit kleiner Beschriftung darunter."""
        def __init__(self, parent, icon, label, cmd):
            tk.Canvas.__init__(self, parent, width=88, height=80, bg=C["side"], highlightthickness=0, bd=0, cursor="hand2")
            self.cmd = cmd
            self.form = rrect(self, 8, 4, 80, 76, 14, fill=C["side"], outline=C["side"], width=2)
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
            self.itemconfigure(self.form, fill=C["soft"] if on else C["side"], outline="#5b5e64" if on else C["side"])
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
            log("Fenster-Fehler: " + traceback.format_exc())
        root.after(100, pump)
    root.report_callback_exception = lambda *a: log("Fenster-Fehler: " + "".join(traceback.format_exception(*a)))

    cfg = config_lesen()
    chat_url = cfg.get("chat_url") or CHAT_URL
    st = dict(busy=False, installiert=installierte_version(), neueste=None, laeuft=False, veraltet=False,
              auto=bool(cfg.get("autoupdate", True)))

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
    def R(seite, x1, y1, x2, y2, r=16, fill=None, rand=None):
        i = rrect(main, x1, y1, x2, y2, r, fill=fill or C["card"], outline=rand or C["line"], width=2)
        main.addtag_withtag("p_" + seite, i)
        return i
    def Wf(seite, x, y, widget, anker="nw"):
        return main.create_window(x, y, window=widget, anchor=anker, tags=("p_" + seite,))

    # Kopfzeile (auf jeder Seite sichtbar)
    main.create_text(36, 26, text="ROK TRACKER", fill=C["ink"], anchor="nw", font=(PIX, 16, "bold"))
    main.create_text(37, 54, text="LAUNCHER", fill=C["mute"], anchor="nw", font=(PIX, 8, "bold"))
    rrect(main, 520, 20, 700, 60, 12, fill=C["card"], outline=C["line"], width=2)
    punkt = main
    kreis = main.create_oval(536, 35, 546, 45, fill=C["mute"], outline="")
    t_tracker = Txt(main, main.create_text(554, 40, text="PRÜFE ...", fill=C["ink"], anchor="w", font=(PIX, 8, "bold")))
    kopf_chat = Knopf(main, "CLAUDE CHAT", lambda: webbrowser.open(chat_url), klein=True)
    main.create_window(716, 40, window=kopf_chat, anchor="w")

    # Fuss (auf jeder Seite sichtbar)
    bar = Balken(main)
    main.create_window(36, 592, window=bar, anchor="nw", width=832)
    status = Txt(main, main.create_text(36, 608, text="Bereit", fill=C["mute"], anchor="nw", font=(FONT, 9), width=832))

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
                log("Fehler: " + traceback.format_exc())
                melden("Fehler, Details in update.log")
            finally:
                st["busy"] = False
                ui(lambda: (bar.stop(), sperren(False)))
                pruefen(False)
        threading.Thread(target=lauf, daemon=True).start()

    def update_ausfuehren():
        text, geaendert, launcher, sha = aktualisieren(melden)
        if sha:
            st["installiert"], st["neueste"] = sha, sha
        melden(text)
        if geaendert and laeuft():
            st["veraltet"] = True
            melden(text + ". Zum Uebernehmen: Neu starten.")
        if launcher and launcher_neu_starten():
            ui(lambda: (log("Launcher aktualisiert, startet neu"), root.destroy()))

    def start_und_oeffnen(ziel="", seite=""):
        melden("Starte Tracker ...")
        ok, fehler = tracker_starten()
        if ok:
            browser_oeffnen(ziel, seite)
            melden("Tracker laeuft. Browser geoeffnet.")
        else:
            melden("Start fehlgeschlagen. " + fehler)

    def neu_starten():
        melden("Beende laufende Version ...")
        beenden()
        if st["auto"]:
            update_ausfuehren()
        st["veraltet"] = False
        start_und_oeffnen()

    def tracker_beenden():
        melden("Beende Tracker ...")
        ok = beenden()
        melden("Tracker beendet." if ok else "Konnte den Tracker nicht beenden.")

    def aktion(name):
        if name == "dashboard":
            arbeit(lambda: start_und_oeffnen(), "Oeffne Dashboard ...")
        elif name == "profil":
            arbeit(lambda: start_und_oeffnen("profil"), "Oeffne Profil ...")
        elif name == "allianz":
            arbeit(lambda: start_und_oeffnen(seite="allianz"), "Oeffne Allianz-Tracker ...")
        elif name == "allianz_import":
            pfade = filedialog.askopenfilenames(title="Allianz-Datei (JSON) waehlen", filetypes=[("JSON-Datei", "*.json"), ("Alle Dateien", "*.*")])
            if pfade:
                def imp():
                    melden("Starte Tracker ...")
                    ok, fehler = tracker_starten()
                    if not ok:
                        return melden("Start fehlgeschlagen. " + fehler)
                    n, f = json_einspielen(pfade)
                    melden(("%d Eintraege uebernommen." % n) + (" Fehler: " + f if f else " Im Dashboard unter Allianz ansehen."))
                arbeit(imp, "Spiele Allianz-Daten ein ...")
        elif name == "allianz_bilder":
            pfade = filedialog.askopenfilenames(title="Allianz-Screenshots waehlen",
                                                filetypes=[("Bilder", "*.png *.jpg *.jpeg *.webp"), ("Alle Dateien", "*.*")])
            if pfade:
                def bilder():
                    n, ziel = bilder_sammeln(pfade)
                    melden("%d Bilder gesammelt in %s" % (n, ziel))
                    def fragen():
                        if messagebox.askyesno("Bilder gesammelt",
                                               "%d Bilder wurden gesammelt.\n\nDie Auswertung macht Claude im Chat. Chat und Ordner jetzt oeffnen, "
                                               "um die Bilder dort hochzuladen?" % n):
                            ordner_oeffnen(ziel)
                            webbrowser.open(chat_url)
                    ui(fragen)
                arbeit(bilder, "Kopiere Bilder ...")
        elif name == "diagnose":
            def d():
                ok, t = diagnose_starten()
                melden(t)
            arbeit(d, "Starte Diagnose ...")


    # ---------------- Seite START
    cx = 296
    for i, farbe in enumerate(("#3b82f6", "#34d399", "#fbbf24")):
        main.create_arc(cx - 70, 96, cx + 70, 236, start=90 + i * 120, extent=116, outline=farbe, width=6, style="arc", tags=("p_play",))
    T("play", cx, 166, "RK", 22, fett=True, anker="center")
    T("play", cx, 268, "ROK TRACKER", 20, fett=True, anker="center")
    T("play", cx, 298, "BARBAREN, BEUTE UND PROFIL", 8, C["mute"], True, "center")
    b_start = Knopf(main, "DASHBOARD ÖFFNEN", lambda: aktion("dashboard"), primaer=True, w=340, h=88, sub="Startet den Tracker und öffnet den Browser")
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
        R("play", x0 + 14, y + 14, x0 + 58, y + 58, 10, C["soft"], C["soft"])
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
    R("upd", 60, 112, 844, 460, 18)
    T("upd", 92, 140, "INSTALLIERT", 8, C["mute"], True)
    t_version = Txt(main, T("upd", 92, 162, "...", 16, fett=True))
    t_version2 = Txt(main, T("upd", 92, 196, "", 10, C["mute"], schrift=FONT))
    b_upd = Knopf(main, "UPDATE SUCHEN", lambda: arbeit(update_ausfuehren, "Suche nach Updates ..."), bg=C["card"])
    Wf("upd", 92, 246, b_upd)
    b_neu = Knopf(main, "NEU STARTEN", lambda: arbeit(neu_starten, "Starte neu ..."), bg=C["card"])
    Wf("upd", 92 + int(b_upd.cget("width")) + 10, 246, b_neu)
    b_stop = Knopf(main, "BEENDEN", lambda: arbeit(tracker_beenden, "Beende Tracker ..."), bg=C["card"])
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
    Wf("upd", 90, 316, tk.Checkbutton(main, text="Beim Neustart automatisch nach Updates suchen", variable=autoupdate, bg=C["card"], fg=C["mute"],
                                      selectcolor=C["soft"], activebackground=C["card"], activeforeground=C["ink"], font=(FONT, 10),
                                      highlightthickness=0, bd=0))
    k_prot = Knopf(main, "PROTOKOLL", lambda: ordner_oeffnen(DATA) if not os.path.exists(LOG) else
                   (os.startfile(LOG) if os.name == "nt" else None), klein=True, bg=C["card"])
    Wf("upd", 92, 380, k_prot)
    Wf("upd", 92 + int(k_prot.cget("width")) + 10, 380, Knopf(main, "DATENORDNER", lambda: ordner_oeffnen(DATA), klein=True, bg=C["card"]))

    menue.add_command(label="Neu starten", command=lambda: arbeit(neu_starten, "Starte neu ..."))
    menue.add_command(label="Nach Update suchen", command=lambda: arbeit(update_ausfuehren, "Suche nach Updates ..."))
    menue.add_separator()
    menue.add_command(label="Tracker beenden", command=lambda: arbeit(tracker_beenden, "Beende Tracker ..."))

    # ---------------- linke Leiste
    logo = tk.Canvas(side, width=96, height=84, bg=C["side"], highlightthickness=0, bd=0)
    logo.pack()
    logo.create_text(48, 44, text="⚡", fill=C["ink"], font=("Segoe UI Symbol", 26))
    navs = {}
    def zeige_seite(name):
        for sname in ("play", "upd"):
            main.itemconfigure("p_" + sname, state="normal" if sname == name else "hidden")
        for k, nv in navs.items():
            nv.setze(k == name)
    for key, icon, label, cmd in (("play", "▶", "START", lambda: zeige_seite("play")),
                                  ("upd", "⬇", "UPDATE", lambda: zeige_seite("upd")),
                                  ("chat", "💬", "CHAT", lambda: webbrowser.open(chat_url))):
        nv = Nav(side, icon, label, cmd)
        nv.pack(pady=2)
        navs[key] = nv
    Nav(side, "⚙", "DATEN", lambda: ordner_oeffnen(DATA)).pack(side="bottom", pady=(0, 10))
    zeige_seite("play")

    # --- Status regelmaessig aktualisieren (im Hintergrund, Anzeige im Fenster)
    def zeige():
        if st["laeuft"]:
            punkt.itemconfigure(kreis, fill=C["good"])
            t_tracker.configure(text="LÄUFT · PORT %d" % PORT)
        else:
            punkt.itemconfigure(kreis, fill=C["mute"])
            t_tracker.configure(text="GESTOPPT")
        inst = (st["installiert"] or "unbekannt")[:7]
        neu = (st["neueste"] or "")[:7]
        t_version.configure(text="Installiert: " + inst)
        if st["veraltet"]:
            t_version2.configure(text="Update geladen, Neustart nötig", fg=C["warn"])
        elif st["neueste"] and st["installiert"] != st["neueste"]:
            t_version2.configure(text="Update verfügbar (%s)" % neu, fg=C["warn"])
        elif st["neueste"]:
            t_version2.configure(text="Aktuell", fg=C["good"])
        else:
            t_version2.configure(text="GitHub nicht erreichbar", fg=C["mute"])
        if not st["busy"]:
            sperren(False)

    def pruefen(mit_version=True):
        def lauf():
            st["laeuft"] = laeuft()
            if mit_version:
                sha, _ = neueste_version()
                if sha:
                    st["neueste"] = sha
            ui(zeige)
        threading.Thread(target=lauf, daemon=True).start()

    def takt():
        if not st["busy"]:
            pruefen(False)
        root.after(3000, takt)

    pump()
    pruefen(True)
    root.after(3000, takt)
    if st["auto"]:
        root.after(600, lambda: arbeit(update_ausfuehren, "Suche nach Updates ..."))
    root.mainloop()

def ohne_fenster():
    beenden()
    aktualisieren()
    ok, fehler = tracker_starten()
    if ok:
        browser_oeffnen()
    else:
        log("Start fehlgeschlagen: " + fehler)

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
            log("Fenster konnte nicht gebaut werden: " + traceback.format_exc())
            ohne_fenster()
    else:
        ohne_fenster()
