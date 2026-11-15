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

def browser_oeffnen(ziel=""):
    webbrowser.open(URL + (("#" + ziel) if ziel else ""))

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
    if os.environ.get("ROK_RELAUNCHED"):
        return False
    try:
        env = dict(os.environ, ROK_RELAUNCHED="1")
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

# Kacheln: (Symbol, Titel, Beschreibung, Aktion, Knopftext). Aktion None = noch nicht verfuegbar.
MODULE = [
    ("⚔", "Barbaren- und Beute-Tracker", "Berichte, Festungen, Spielerkaempfe und Ark of Osiris", "dashboard", "Oeffnen"),
    ("\U0001F464", "Profil-Tracker", "Macht, Kill Points und Verlauf aus deinen Profil-Screenshots", "profil", "Oeffnen"),
    ("\U0001F50D", "Diagnose", "Prueft den mailcache und schreibt diagnose.txt auf den Desktop", "diagnose", "Starten"),
    ("✨", "Weitere Tools", "Hier kommen die naechsten Funktionen hin", None, "Bald"),
]

def mit_fenster():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("RoK Tracker Launcher")
    root.configure(bg=C["bg"])
    root.resizable(False, False)
    W, H = 760, 700
    root.geometry("%dx%d+%d+%d" % (W, H, (root.winfo_screenwidth() - W) // 2, max(20, (root.winfo_screenheight() - H) // 3)))

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
    st = dict(busy=False, installiert=installierte_version(), neueste=None, laeuft=False, veraltet=False,
              auto=bool(cfg.get("autoupdate", True)))

    # --- Hilfen fuer Aussehen
    def karte(parent, **kw):
        return tk.Frame(parent, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, **kw)

    class Knopf(tk.Label):
        def __init__(self, parent, text, cmd, primaer=False, klein=False):
            self.farbe = C["acc"] if primaer else C["soft"]
            self.hover = C["acc2"] if primaer else C["line"]
            tk.Label.__init__(self, parent, text=text, bg=self.farbe, fg="#ffffff" if primaer else C["ink"],
                              font=(FONT, 9 if klein else 10, "bold" if primaer else "normal"),
                              padx=14 if not klein else 10, pady=8 if not klein else 5, cursor="hand2")
            self.cmd, self.aktiv = cmd, True
            self.bind("<Button-1>", lambda e: self.cmd() if self.aktiv else None)
            self.bind("<Enter>", lambda e: self.configure(bg=self.hover if self.aktiv else self.farbe))
            self.bind("<Leave>", lambda e: self.configure(bg=self.farbe))
        def setze(self, aktiv=True, farbe=None):
            self.aktiv = aktiv
            if farbe:
                self.farbe = farbe
            self.configure(bg=self.farbe, fg=C["ink"] if aktiv else C["mute"], cursor="hand2" if aktiv else "arrow")

    # --- Kopf
    kopf = tk.Frame(root, bg=C["bg"])
    kopf.pack(fill="x", padx=24, pady=(22, 10))
    ring = tk.Canvas(kopf, width=60, height=60, bg=C["bg"], highlightthickness=0)
    ring.pack(side="left")
    for i, farbe in enumerate(("#3b82f6", "#34d399", "#fbbf24")):
        ring.create_arc(3, 3, 57, 57, start=90 + i * 120, extent=120, outline=farbe, width=4, style="arc")
    ring.create_text(30, 30, text="RK", fill=C["ink"], font=(FONT, 14, "bold"))
    titel = tk.Frame(kopf, bg=C["bg"])
    titel.pack(side="left", padx=14)
    tk.Label(titel, text="RoK Tracker", bg=C["bg"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w")
    tk.Label(titel, text="Launcher", bg=C["bg"], fg=C["mute"], font=(FONT, 10)).pack(anchor="w")

    # --- Status
    reihe = tk.Frame(root, bg=C["bg"])
    reihe.pack(fill="x", padx=24, pady=6)
    reihe.columnconfigure(0, weight=1, uniform="s")
    reihe.columnconfigure(1, weight=1, uniform="s")

    k1 = karte(reihe)
    k1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    tk.Label(k1, text="Tracker", bg=C["card"], fg=C["mute"], font=(FONT, 9)).pack(anchor="w", padx=14, pady=(10, 0))
    z1 = tk.Frame(k1, bg=C["card"])
    z1.pack(anchor="w", padx=14, pady=(2, 12))
    punkt = tk.Canvas(z1, width=12, height=12, bg=C["card"], highlightthickness=0)
    punkt.pack(side="left", pady=2)
    kreis = punkt.create_oval(1, 1, 11, 11, fill=C["mute"], outline="")
    t_tracker = tk.Label(z1, text="Pruefe ...", bg=C["card"], fg=C["ink"], font=(FONT, 12, "bold"))
    t_tracker.pack(side="left", padx=8)

    k2 = karte(reihe)
    k2.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    tk.Label(k2, text="Version", bg=C["card"], fg=C["mute"], font=(FONT, 9)).pack(anchor="w", padx=14, pady=(10, 0))
    t_version = tk.Label(k2, text="...", bg=C["card"], fg=C["ink"], font=(FONT, 12, "bold"))
    t_version.pack(anchor="w", padx=14)
    t_version2 = tk.Label(k2, text="", bg=C["card"], fg=C["mute"], font=(FONT, 9))
    t_version2.pack(anchor="w", padx=14, pady=(0, 10))

    # --- Aktionen
    leiste = tk.Frame(root, bg=C["bg"])
    leiste.pack(fill="x", padx=24, pady=(10, 4))

    def melden(text):
        ui(lambda: status.configure(text=text))

    def arbeit(fn, text):
        """Fuehrt fn in einem Thread aus, solange nichts anderes laeuft."""
        if st["busy"]:
            return
        st["busy"] = True
        def lauf():
            ui(lambda: (status.configure(text=text), bar.start(12), sperren(True)))
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

    def start_und_oeffnen(ziel=""):
        melden("Starte Tracker ...")
        ok, fehler = tracker_starten()
        if ok:
            browser_oeffnen(ziel)
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
        elif name == "diagnose":
            def d():
                ok, t = diagnose_starten()
                melden(t)
            arbeit(d, "Starte Diagnose ...")

    b_start = Knopf(leiste, "▶  Dashboard öffnen", lambda: aktion("dashboard"), primaer=True)
    b_start.pack(side="left")
    b_neu = Knopf(leiste, "↻  Neu starten", lambda: arbeit(neu_starten, "Starte neu ..."))
    b_neu.pack(side="left", padx=8)
    b_upd = Knopf(leiste, "⬇  Update", lambda: arbeit(update_ausfuehren, "Suche nach Updates ..."))
    b_upd.pack(side="left")
    b_stop = Knopf(leiste, "■  Beenden", lambda: arbeit(tracker_beenden, "Beende Tracker ..."))
    b_stop.pack(side="left", padx=8)
    alle = (b_start, b_neu, b_upd, b_stop)

    def sperren(an):
        for b in alle:
            b.setze(not an)
        if not an:
            b_start.setze(True, C["acc"])
            b_start.configure(fg="#ffffff")
            b_upd.setze(True, C["warn"] if st["veraltet"] or (st["installiert"] != st["neueste"] and st["neueste"]) else C["soft"])
            if b_upd.farbe == C["warn"]:
                b_upd.configure(fg="#111111")

    autoupdate = tk.BooleanVar(value=cfg.get("autoupdate", True))
    def auto_geaendert(*a):
        st["auto"] = bool(autoupdate.get())
        c = config_lesen()
        c["autoupdate"] = bool(autoupdate.get())
        config_schreiben(c)
    autoupdate.trace_add("write", auto_geaendert)
    tk.Checkbutton(root, text="Beim Neustart automatisch nach Updates suchen", variable=autoupdate, bg=C["bg"], fg=C["mute"],
                   selectcolor=C["soft"], activebackground=C["bg"], activeforeground=C["ink"], font=(FONT, 9),
                   highlightthickness=0, bd=0).pack(anchor="w", padx=22, pady=(4, 0))

    # --- Kacheln
    tk.Label(root, text="Tools", bg=C["bg"], fg=C["ink"], font=(FONT, 12, "bold")).pack(anchor="w", padx=24, pady=(14, 4))
    raster = tk.Frame(root, bg=C["bg"])
    raster.pack(fill="x", padx=24)
    raster.columnconfigure(0, weight=1, uniform="m")
    raster.columnconfigure(1, weight=1, uniform="m")
    for i, (symbol, name, beschr, akt, knopf) in enumerate(MODULE):
        k = karte(raster)
        k.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, 6) if i % 2 == 0 else (6, 0), pady=6)
        oben = tk.Frame(k, bg=C["card"])
        oben.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(oben, text=symbol, bg=C["soft"], fg=C["ink"], font=("Segoe UI Emoji", 16), width=2).pack(side="left")
        tk.Label(oben, text=name, bg=C["card"], fg=C["ink"] if akt else C["mute"], font=(FONT, 11, "bold"),
                 wraplength=230, justify="left").pack(side="left", padx=10)
        tk.Label(k, text=beschr, bg=C["card"], fg=C["mute"], font=(FONT, 9), wraplength=300, justify="left").pack(anchor="w", padx=14, pady=(6, 8))
        kn = Knopf(k, knopf, (lambda a=akt: aktion(a)) if akt else (lambda: None), klein=True)
        if not akt:
            kn.setze(False)
        kn.pack(anchor="w", padx=14, pady=(0, 12))

    # --- Fuss
    fuss = tk.Frame(root, bg=C["bg"])
    fuss.pack(side="bottom", fill="x", padx=24, pady=(0, 14))
    bar = ttk.Progressbar(fuss, mode="indeterminate", length=712)
    try:
        stil = ttk.Style()
        stil.theme_use("clam")
        stil.configure("Horizontal.TProgressbar", troughcolor=C["soft"], background=C["acc"], bordercolor=C["bg"],
                       lightcolor=C["acc"], darkcolor=C["acc"])
    except Exception:
        pass
    bar.pack(fill="x", pady=(0, 6))
    status = tk.Label(fuss, text="Bereit", bg=C["bg"], fg=C["mute"], font=(FONT, 9), anchor="w", justify="left", wraplength=500)
    status.pack(side="left")
    Knopf(fuss, "Datenordner", lambda: ordner_oeffnen(DATA), klein=True).pack(side="right")
    Knopf(fuss, "Protokoll", lambda: ordner_oeffnen(DATA) if not os.path.exists(LOG) else
          (os.startfile(LOG) if os.name == "nt" else None), klein=True).pack(side="right", padx=6)

    # --- Status regelmaessig aktualisieren (im Hintergrund, Anzeige im Fenster)
    def zeige():
        if st["laeuft"]:
            punkt.itemconfigure(kreis, fill=C["good"])
            t_tracker.configure(text="Läuft  (Port %d)" % PORT)
        else:
            punkt.itemconfigure(kreis, fill=C["mute"])
            t_tracker.configure(text="Gestoppt")
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
    if cfg.get("autoupdate", True):
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
