# -*- coding: utf-8 -*-
"""Starter fuer den RoK-Tracker: beendet eine schon laufende Version, holt die neueste rok_barbs.py und startet sie.
Doppelklick genuegt, es oeffnet sich kein Konsolenfenster. Laeuft das Programm schon, wird es neu gestartet.
Ohne Internet startet einfach die vorhandene Version. Lege diese Datei in denselben Ordner wie rok_barbs.py."""
import hashlib, json, os, runpy, subprocess, sys, time, urllib.request

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

if __name__ == "__main__":
    beenden()
    aktualisieren()
    sys.argv = [os.path.join(HIER, "rok_barbs.py")] + sys.argv[1:]
    sys.path.insert(0, HIER)
    runpy.run_path(sys.argv[0], run_name="__main__")
