# -*- coding: utf-8 -*-
"""Prueft den gesamten mailcache und zeigt, wo Spielerkampf-Berichte (Kill Points) liegen und welche das
Dashboard nicht zaehlt. Legt rok_barbs.py und diese Datei in denselben Ordner, dann Doppelklick.
Ergebnis: diagnose.txt auf dem Desktop (bitte hochladen)."""
import os, sys, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rok_barbs as R

def main():
    folder = sys.argv[1] if len(sys.argv)>1 else R.DEFAULT_FOLDER
    files = []
    for root in [folder]:
        for dp, _, fs in os.walk(root):
            for f in fs:
                if R.MAIL_NAME.match(f):
                    files.append(os.path.join(dp, f))
    boxes = collections.Counter(); errs = []
    modes = collections.defaultdict(lambda: collections.Counter())
    months = collections.defaultdict(lambda: collections.Counter())
    excl = collections.Counter()
    for i, p in enumerate(files):
        try:
            m = R.parse_mail(p)
        except Exception:
            errs.append(os.path.basename(p)); continue
        key = (m.get("type"), m.get("box"), m.get("prevBox"))
        boxes[key] += 1
        c = (m.get("body") or {}).get("content") if isinstance(m.get("body"), dict) else None
        if not (isinstance(c, dict) and isinstance(c.get("Attacks"), dict)):
            continue
        mode = c.get("Role", "?")
        pl = [a for a in c["Attacks"].values() if not ("NpcType" in a.get("CIdt", {}) and a["CIdt"].get("PId", 0) < 0)]
        if not pl:
            continue
        counted = R.extract(m) is not None
        acc = str(m.get("receiver", ""))
        k = (mode, key[1], key[2], acc, "gezaehlt" if counted else "NICHT gezaehlt")
        st = modes[k]
        st["mails"] += 1; st["runden"] += len(pl)
        st["kp_eigene(Damage)"] += sum(a["Damage"]["KillScore"] for a in pl)
        st["kp_gegner(Kill)"] += sum(a["Kill"]["KillScore"] for a in pl)
        st["rally(STs>1)"] += 1 if len(c.get("STs", {})) > 1 else 0
        t = datetime.datetime.fromtimestamp(m["time"] / 1e6)
        months[(mode, t.strftime("%Y-%m"))]["kp"] += sum(a["Damage"]["KillScore"] for a in pl)
        months[(mode, t.strftime("%Y-%m"))]["mails"] += 1
        if not counted:
            excl[key] += 1
    out = ["Dateien gesamt: %d, nicht lesbar: %d" % (len(files), len(errs)), "", "== Mails nach (Typ, Box, vorherige Box) =="]
    out += ["%6d  %s" % (n, k) for k, n in boxes.most_common()]
    out += ["", "== Spielerkampf-Mails nach (Modus, Box, vorherige Box, Konto, Status) =="]
    for k, st in sorted(modes.items(), key=lambda kv: -kv[1]["kp_eigene(Damage)"]):
        out.append("%s\n     %s" % (k, dict(st)))
    out += ["", "== Eigene Kill Points pro Modus und Monat =="]
    out += ["%s %s: %d Mails, KP %d" % (k[0], k[1], v["mails"], v["kp"]) for k, v in sorted(months.items())]
    out += ["", "== Nicht gezaehlte Spielerkampf-Mails =="] + ["%6d  %s" % (n, k) for k, n in excl.most_common()]
    if errs:
        out += ["", "Nicht lesbar (erste 20):"] + errs[:20]
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    path = os.path.join(desk if os.path.isdir(desk) else os.path.expanduser("~"), "diagnose.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n".join(out[:40])); print("\nFertig. Datei:", path)
    input("Enter zum Beenden...")

if __name__ == "__main__":
    main()
