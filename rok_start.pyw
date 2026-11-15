# -*- coding: utf-8 -*-
"""Starter fuer den RoK-Tracker: beendet eine schon laufende Version, holt die neueste rok_barbs.py und startet sie.
Doppelklick genuegt, es oeffnet sich kein Konsolenfenster. Laeuft das Programm schon, wird es neu gestartet.
Ohne Internet startet einfach die vorhandene Version. Lege diese Datei in denselben Ordner wie rok_barbs.py."""
import hashlib, json, os, runpy, subprocess, sys, threading, time, urllib.request

# Ordner-URL, unter der die aktuelle Datei liegt (mit / am Ende)
UPDATE_URL = "https://raw.githubusercontent.com/JSON2207/rok-tracker/main/"
DATEIEN = ["rok_barbs.py", "rok_diagnose.py"]       # fehlt eine Datei online, wird sie uebersprungen

HIER = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.environ.get("APPDATA", HIER), "RoK-Tracker", "update.log")
PORT = 8765
if "--port" in sys.argv:
    try:
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
    except Exception:
        pass

def log(text):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass

def laeuft():
    """True, wenn auf dem Port schon der RoK-Tracker antwortet."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % PORT, timeout=1.5) as r:
            return "folder_ok" in json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False

def warten(sek):
    ende = time.time() + sek
    while time.time() < ende:
        if not laeuft():
            return True
        time.sleep(0.25)
    return not laeuft()

def beenden():
    """Beendet eine laufende Version. Neue Versionen sagt man es per Anfrage, aeltere werden ueber den Port gefunden."""
    if not laeuft():
        return
    log("laufende Version wird beendet")
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/api/quit" % PORT, data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
    if warten(4):
        return
    if os.name == "nt":                                  # aeltere Version ohne /api/quit
        try:
            kw = dict(capture_output=True, text=True, creationflags=0x08000000)
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], **kw).stdout
            pids = {l.split()[-1] for l in out.splitlines() if ":%d " % PORT in l and "LISTENING" in l}
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/F"], **kw)
        except Exception as e:
            log("Beenden ueber Port fehlgeschlagen: %s" % e)
        warten(4)

def holen(name):
    url = UPDATE_URL.rstrip("/") + "/" + name + "?t=" + str(int(time.time()))     # Zeitstempel gegen Zwischenspeicher
    req = urllib.request.Request(url, headers={"User-Agent": "rok-tracker-launcher", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return r.read()

def aktualisieren():
    if not UPDATE_URL:
        return
    for name in DATEIEN:
        try:
            neu = holen(name)
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
            tmp = pfad + ".neu"
            with open(tmp, "wb") as f:
                f.write(neu)
            os.replace(tmp, pfad)
            log("aktualisiert: " + name)
        except Exception as e:
            log("Update %s uebersprungen: %s" % (name, e))

APP = os.path.join(HIER, "rok_barbs.py")

def app_starten(fehler):
    try:
        sys.argv = [APP] + [x for x in sys.argv[1:]]
        sys.path.insert(0, HIER)
        runpy.run_path(APP, run_name="__main__")
    except SystemExit:
        pass
    except BaseException as e:
        fehler.append("%s: %s" % (type(e).__name__, e))
        log("Start fehlgeschlagen: %s: %s" % (type(e).__name__, e))

def mit_fenster(tk, ttk, messagebox):
    """Kleines Fenster, das zeigt, was gerade passiert, und schliesst, sobald das Dashboard bereit ist."""
    root = tk.Tk()
    root.title("RoK-Tracker")
    root.resizable(False, False)
    w, h = 340, 100
    root.geometry("%dx%d+%d+%d" % (w, h, (root.winfo_screenwidth() - w) // 2, (root.winfo_screenheight() - h) // 3))
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    text = tk.StringVar(value="Starte ...")
    tk.Label(root, textvariable=text, font=("Segoe UI", 11)).pack(pady=(18, 10))
    bar = ttk.Progressbar(root, mode="indeterminate", length=280)
    bar.pack()
    bar.start(12)
    st, fehler, app = {"msg": "Starte ...", "fertig": False}, [], [None]

    def arbeit():
        try:
            st["msg"] = "Beende laufende Version ..."
            beenden()
            st["msg"] = "Suche nach Updates ..."
            aktualisieren()
            st["msg"] = "Starte Programm ..."
            t = threading.Thread(target=app_starten, args=(fehler,))
            app[0] = t
            t.start()
            ende = time.time() + 90
            while time.time() < ende and t.is_alive() and not laeuft():
                time.sleep(0.3)
            if laeuft():
                st["msg"] = "Fertig. Das Dashboard oeffnet sich ..."
                time.sleep(1.2)
        except Exception as e:
            fehler.append(repr(e))
        st["fertig"] = True

    def tick():
        text.set(st["msg"])
        if st["fertig"]:
            root.destroy()
        else:
            root.after(150, tick)

    threading.Thread(target=arbeit, daemon=True).start()
    tick()
    root.mainloop()
    if fehler:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("RoK-Tracker", "Der Start ist fehlgeschlagen:\n\n" + "\n".join(fehler) +
                             "\n\nWeitere Hinweise stehen in " + LOG)
        r.destroy()
    if app[0]:
        app[0].join()

if __name__ == "__main__":
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception:
        tk = None
    if tk is None:                       # ohne tkinter: wie bisher, nur ohne Fenster
        beenden()
        aktualisieren()
        app_starten([])
    else:
        mit_fenster(tk, ttk, messagebox)
