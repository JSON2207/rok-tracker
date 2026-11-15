#!/usr/bin/env python3
"""
RoK Barbaren-Tracker
Überwacht den mailcache-Ordner von Rise of Kingdoms, liest neue Barbarenberichte
und zeigt Tages- und Gesamtauswertungen in einer lokalen Webseite.

Start:   python rok_barbs.py
Optional: python rok_barbs.py --folder "D:\\anderer\\pfad" --port 8765

Das Programm liest nur Dateien. Es verändert nichts im Spielordner.
Benötigt nur Python 3.8+ (keine zusätzlichen Pakete).
"""
import argparse, json, os, re, shutil, sqlite3, struct, sys, threading, time, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DEFAULT_FOLDER = r"C:\Program Files (x86)\Rise of Kingdoms\Rise of Kingdoms Game\save\mailcache"
# Feste Ablage der Statistik, unabhängig davon, wo das Skript liegt (Windows: %APPDATA%\RoK-Tracker).
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "RoK-Tracker")

# ---------------------------------------------------------------- Namenslisten
# Wird ergänzt, sobald weitere IDs bestätigt sind. Unbekannte IDs erscheinen als "Item #ID".
# unit = Wert pro Stück (Speedup-Minuten, Rohstoffmenge, XP, Edelsteine), kind = Auswertungsgruppe.
ITEMS = {
    71:  {"name": "Training-Speedup 1 Min", "unit": 1,    "kind": "speed_training"},
    72:  {"name": "Training-Speedup 5 Min (vermutet)", "unit": 5, "kind": "speed_training"},
    34:  {"name": "Universal-Speedup 1 Min", "unit": 1,   "kind": "speed_universal"},
    128: {"name": "XP-Buch 100",   "unit": 100,  "kind": "xp_books"},
    130: {"name": "XP-Buch 1.000", "unit": 1000, "kind": "xp_books"},
    26:  {"name": "Edelsteine (10)", "unit": 10,  "kind": "gems"},
    142: {"name": "Holz (1.000)",  "unit": 1000, "kind": "wood"},
    143: {"name": "Stein (750)",   "unit": 750,  "kind": "stone"},
    144: {"name": "Gold (500)",    "unit": 500,  "kind": "gold"},
    92:  {"name": "Heilungs-Speedup 5 Min", "unit": 5,   "kind": "speed_healing"},
    182: {"name": "Notfall-AP-Wiederherstellung (50)", "unit": 50, "kind": "ap"},
    7006: {"name": "Ressourcenpaket Lvl 2", "unit": 1,   "kind": "packs"},
    7005: {"name": "Ressourcenpaket Lvl 1 (vermutet)", "unit": 1, "kind": "packs"},
}
HEROES = {8: "Richard I", 6: "Yi Seong-Gye", 141: "Thutmose III"}
FOES = {597: "Barbarians", 603: "Bone Archers", 606: "Beast Riders"}
PROFILE_FIELDS = [("power", "Macht"), ("highest_power", "Höchste Macht"), ("kill_points", "Kill Points"), ("dead", "Tote"),
                  ("healed", "Geheilt"), ("building_power", "Gebäude-Macht"), ("tech_power", "Technologie-Macht"),
                  ("troop_power", "Truppen-Macht"), ("commander_power", "Kommandanten-Macht"), ("victory", "Siege"),
                  ("defeat", "Niederlagen"), ("scouts", "Späher-Einsätze"),
                  ("kills_t1", "T1-Kills"), ("kills_t2", "T2-Kills"), ("kills_t3", "T3-Kills"), ("kills_t4", "T4-Kills"),
                  ("kills_t5", "T5-Kills"), ("helps", "Hilfen"), ("gathered", "Gesammelt"), ("rss_assist", "Ressourcenhilfe"),
                  ("ap", "Aktionspunkte")]
MODES = {"dungeon": "Ark of Osiris"}     # alle anderen Modi heißen "Spielerkampf (<Kennung>)"
KINDS = {"speed_training", "speed_universal", "speed_healing", "wood", "stone", "gold", "food", "gems",
         "xp_books", "packs", "ap", "other"}
NAMES_PATH = None     # wird in main() gesetzt: <Datenordner>\namen.json

def load_names(path):
    """Eigene Namen aus namen.json über die eingebauten legen (Datei hat Vorrang)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    for k, v in d.get("items", {}).items():
        ITEMS[int(k)] = v
    for k, v in d.get("heroes", {}).items():
        HEROES[int(k)] = v
    for k, v in d.get("foes", {}).items():
        FOES[int(k)] = v

_names_lock = threading.Lock()
def save_name(kind, key, name, unit=None, ikind=None):
    name = str(name).strip()[:60]
    key = int(key)
    if not name:
        raise ValueError("Name fehlt")
    with _names_lock:
        try:
            with open(NAMES_PATH, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("items", {}); d.setdefault("heroes", {}); d.setdefault("foes", {})
        if kind == "item":
            unit = float(unit) if unit not in (None, "") else 1
            unit = int(unit) if unit == int(unit) else unit
            ikind = ikind if ikind in KINDS else "other"
            d["items"][str(key)] = {"name": name, "unit": unit, "kind": ikind}
        elif kind in ("hero", "foe"):
            d[kind + "es" if kind == "hero" else "foes"][str(key)] = name
        else:
            raise ValueError("Unbekannte Art")
        with open(NAMES_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    load_names(NAMES_PATH)

# ---------------------------------------------------------------- Parser
def _val(b, i):
    t = b[i]; i += 1
    if t == 3:
        return struct.unpack(">d", b[i:i + 8])[0], i + 8
    if t == 4:
        n = struct.unpack("<I", b[i:i + 4])[0]; i += 4
        return b[i:i + n].decode("utf-8", "replace"), i + n
    if t == 1:
        return bool(b[i]), i + 1
    if t == 0:
        return None, i
    if t == 5:
        d = {}
        while b[i] != 0xFF:
            k, i = _val(b, i)
            v, i = _val(b, i)
            d[k] = v
        return d, i + 1
    raise ValueError("Unbekanntes Tag %d bei %d" % (t, i))

def parse_mail(path):
    with open(path, "rb") as f:
        b = f.read()
    mail, end = _val(b, 9)          # 9 Byte Kopf, danach die Tabelle
    if end != len(b):
        raise ValueError("Datei unvollständig")
    return mail

def _account(mail):
    return str(mail.get("receiver", "")).replace("player_", "")

def extract_battle(mail):
    """Kampfbericht: Barbaren (Lager/Festung) und Spieler, solo oder Rally."""
    if mail.get("type") != "Battle":
        return None
    c = mail["body"]["content"]
    me, acc = c["SelfChar"], _account(mail)
    sts = c.get("STs", {})
    mine = next((v for v in sts.values() if str(int(v.get("PId", 0))) == acc), me)
    parts = max(len(sts), 1)
    role = "solo" if parts == 1 else ("host" if str(int(me.get("PId", 0))) == acc else "joined")
    battles, pvp = [], []
    for att in c.get("Attacks", {}).values():
        foe, k, d = att.get("CIdt", {}), att["Kill"], att["Damage"]
        if "NpcType" in foe and foe.get("PId", 0) < 0:
            loot = [(int(v["SubType"]), int(v["Value"])) for v in att.get("NpcKillLoot", {}).values()
                    if v.get("Type") == 2]
            battles.append(dict(
                foe_hid=int(foe.get("HId", 0)), foe_lvl=int(foe.get("HLv", 0)),
                foe_kind="fort" if foe.get("NpcBType") == 2 else "camp",
                foe_troops=int(k["Max"]), own_troops=int(d["Max"]), remaining=int(d["Cnt"]),
                hurt=int(d["Hurt"]), severe=int(d["BadHurt"]), heal=int(d["Healing"]),
                dead=int(d["Death"]), power=int(d["Power"]), xp=int(att.get("NpcAtkExp", 0)), loot=loot))
        else:   # Spieler als Gegner. Feldzuordnung an einem Ark-of-Osiris-Bericht gegen das Spiel geprüft:
                # Damage = eigene Seite, Kill = Gegner-Seite; KillScore in Damage = deine Kill Points.
            pvp.append(dict(
                foe_name=str(foe.get("PName", "")), foe_abbr=str(foe.get("Abbr", "")),
                foe_castle=int(foe.get("CastleLevel", 0)), side=int(att.get("Def", 0)),
                own_troops=int(d["Max"]), own_dead=int(d["Death"]), own_hurt=int(d["Hurt"]),
                own_severe=int(d["BadHurt"]), enemy_troops=int(k["Max"]), enemy_dead=int(k["Death"]),
                enemy_hurt=int(k["Hurt"]), enemy_severe=int(k["BadHurt"]), mode=str(c.get("Role", "")),
                kp_gained=int(d["KillScore"]), kp_lost=int(k["KillScore"]), power=int(d["Power"])))
    if not battles and not pvp:
        return None
    return dict(kind="battle", mail_id=str(mail["id"]), ts=mail["time"] / 1e6, account=acc,
                acc_name=mine.get("PName", ""), role=role, parts=parts, hero1=int(mine.get("HId", 0)),
                battles=battles, pvp=pvp)

def extract_fort_reward(mail):
    """Belohnungs-Mail einer zerstörten Barbarenfestung (englischer Spieltext) oder None."""
    if mail.get("type") != "System":
        return None
    txt = mail["body"]["content"] if isinstance(mail["body"]["content"], str) else ""
    m = re.search(r"[Ll]evel (\d+) barbarian fort at X:(\d+) Y:(\d+)", txt)
    if not m:
        return None
    pct = re.search(r"dealt (\d+)%", txt)
    tier = re.search(r"Tier (\d+)", txt)
    loot = []
    for att in mail.get("attachments", {}).values():
        loot += [(int(v["SubType"]), int(v["Value"])) for v in att.get("loot", {}).values() if v.get("Type") == 2]
    return dict(kind="fort", mail_id=str(mail["id"]), ts=mail["time"] / 1e6, account=_account(mail),
                level=int(m.group(1)), x=int(m.group(2)), y=int(m.group(3)),
                pct=int(pct.group(1)) if pct else None, tier=int(tier.group(1)) if tier else None, loot=loot)

MAIL_NAME = re.compile(r"^persistent[._]mail[._]\d+", re.I)   # schließt MailBriefInfo aus
DROP_KEYS = {"Udid", "Avatar", "AppUid", "CTK", "Idt", "receiver", "PId", "AId", "CId", "CtId"}

def sanitize(o, depth=0):
    """Für die Weitergabe: persönliche Kennungen entfernen, lange Texte kürzen."""
    if isinstance(o, dict):
        return {str(k): sanitize(v, depth + 1) for k, v in list(o.items())[:60] if k not in DROP_KEYS}
    if isinstance(o, str):
        return o[:160]
    return o

def extract(mail):
    """Nur Berichte aus dem Reiter REPORT. Alles andere (System, Alliance, Player, Events ...) wird ignoriert."""
    box = mail.get("box")
    if box != "Report" and not (box == "Archive" and mail.get("prevBox") == "Report"):
        return None     # Favoriten zählen nur, wenn sie ursprünglich ein Bericht waren
    return extract_battle(mail) or extract_fort_reward(mail)

# ---------------------------------------------------------------- Datenbank
class DB:
    VERSION = 5

    def __init__(self, path):
        self.lock = threading.Lock()
        self._ign = None
        self.c = sqlite3.connect(path, check_same_thread=False)
        if self.c.execute("PRAGMA user_version").fetchone()[0] in (0, 1):
            # Nur die allerersten Testversionen werden neu aufgebaut. Ab Version 2 bleibt alles erhalten.
            for t in ("reports", "battles", "forts", "loot"):
                self.c.execute("DROP TABLE IF EXISTS " + t)
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS reports(mail_id TEXT PRIMARY KEY, ts REAL, day TEXT, account TEXT, acc_name TEXT);
        CREATE TABLE IF NOT EXISTS battles(id INTEGER PRIMARY KEY AUTOINCREMENT, mail_id TEXT, ts REAL, day TEXT,
          account TEXT, role TEXT, parts INT, hero1 INT, foe_hid INT, foe_kind TEXT, foe_lvl INT, foe_troops INT,
          own_troops INT, remaining INT, hurt INT, severe INT, heal INT, dead INT, power INT, xp INT);
        CREATE TABLE IF NOT EXISTS forts(id INTEGER PRIMARY KEY AUTOINCREMENT, mail_id TEXT, ts REAL, day TEXT,
          account TEXT, level INT, x INT, y INT, pct INT, tier INT);
        CREATE TABLE IF NOT EXISTS loot(src TEXT, src_id INT, day TEXT, account TEXT, item_id INT, qty INT);
        CREATE TABLE IF NOT EXISTS pvp(id INTEGER PRIMARY KEY AUTOINCREMENT, mail_id TEXT, ts REAL, day TEXT,
          account TEXT, role TEXT, parts INT, foe_name TEXT, foe_abbr TEXT, foe_castle INT, side INT,
          own_troops INT, own_dead INT, own_hurt INT, own_severe INT, enemy_troops INT, enemy_dead INT,
          kp_gained INT, kp_lost INT, power INT, enemy_hurt INT, enemy_severe INT, mode TEXT);
        CREATE TABLE IF NOT EXISTS processed(name TEXT PRIMARY KEY, size INT, mtime INT, status TEXT);
        CREATE TABLE IF NOT EXISTS samples(k TEXT PRIMARY KEY, js TEXT);
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS profile(account TEXT, day TEXT, name TEXT, data TEXT, PRIMARY KEY(account, day));
        CREATE INDEX IF NOT EXISTS ix_b ON battles(day, account);
        CREATE INDEX IF NOT EXISTS ix_f ON forts(day, account);
        CREATE INDEX IF NOT EXISTS ix_l ON loot(day, account);
        CREATE INDEX IF NOT EXISTS ix_p ON pvp(day, account);
        """)
        # Aufräumen: alles, was kein Kampf- oder Festungsbericht ist, aus der Statistik löschen
        self.c.executescript("""
        DROP TABLE IF EXISTS others;
        DELETE FROM loot WHERE src NOT IN ('b','f');
        DELETE FROM reports WHERE mail_id NOT IN (SELECT mail_id FROM battles UNION SELECT mail_id FROM pvp
                                                  UNION SELECT mail_id FROM forts);
        """)
        if self.c.execute("PRAGMA user_version").fetchone()[0] < 5:
            # Version 5: Spielerkämpfe werden mit korrekter Feldzuordnung neu eingelesen (alle Dateien einmal neu)
            for col in ("enemy_hurt INT", "enemy_severe INT", "mode TEXT"):
                try:
                    self.c.execute("ALTER TABLE pvp ADD COLUMN " + col)
                except sqlite3.OperationalError:
                    pass
            self.c.executescript("""
            DELETE FROM loot WHERE src='b' AND src_id IN (SELECT id FROM battles WHERE mail_id IN (SELECT mail_id FROM pvp));
            DELETE FROM battles WHERE mail_id IN (SELECT mail_id FROM pvp);
            DELETE FROM reports WHERE mail_id IN (SELECT mail_id FROM pvp);
            DELETE FROM pvp;
            DELETE FROM processed;
            """)
        self.c.execute("PRAGMA user_version=%d" % self.VERSION)
        self.c.commit()

    # --- gemerkte Dateien: beim Neustart werden nur neue oder geänderte Dateien gelesen
    def load_processed(self):
        with self.lock:
            return {n: (sz, mt) for n, sz, mt in self.c.execute("SELECT name,size,mtime FROM processed")}

    def mark_processed(self, name, size, mtime, status):
        with self.lock:
            self.c.execute("INSERT OR REPLACE INTO processed VALUES(?,?,?,?)", (name, size, mtime, status))
            self.c.commit()

    def add_sample(self, key, obj):
        """Merkt sich je Berichtsart ein bereinigtes Beispiel. Gibt True zurück, wenn es neu war."""
        with self.lock:
            if self.c.execute("SELECT 1 FROM samples WHERE k=?", (key,)).fetchone():
                return False
            self.c.execute("INSERT INTO samples VALUES(?,?)", (key, json.dumps(obj, ensure_ascii=False, default=str)))
            self.c.commit()
            return True

    def all_samples(self):
        with self.lock:
            return {k: json.loads(js) for k, js in self.c.execute("SELECT k, js FROM samples ORDER BY k")}

    def add(self, r):
        day = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
        acc = r["account"]
        with self.lock:
            if self.c.execute("SELECT 1 FROM reports WHERE mail_id=?", (r["mail_id"],)).fetchone():
                return False
            self.c.execute("INSERT INTO reports VALUES(?,?,?,?,?)", (r["mail_id"], r["ts"], day, acc, r.get("acc_name", "")))
            lp = lambda src, i, loot: [self.c.execute("INSERT INTO loot VALUES(?,?,?,?,?,?)", (src, i, day, acc, it, q))
                                       for it, q in loot]
            if r["kind"] == "battle":
                for b in r["battles"]:
                    cur = self.c.execute(
                        "INSERT INTO battles(mail_id,ts,day,account,role,parts,hero1,foe_hid,foe_kind,foe_lvl,"
                        "foe_troops,own_troops,remaining,hurt,severe,heal,dead,power,xp) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["mail_id"], r["ts"], day, acc, r["role"], r["parts"], r["hero1"], b["foe_hid"],
                         b["foe_kind"], b["foe_lvl"], b["foe_troops"], b["own_troops"], b["remaining"], b["hurt"],
                         b["severe"], b["heal"], b["dead"], b["power"], b["xp"]))
                    lp("b", cur.lastrowid, b["loot"])
                for x in r["pvp"]:
                    self.c.execute(
                        "INSERT INTO pvp(mail_id,ts,day,account,role,parts,foe_name,foe_abbr,foe_castle,side,own_troops,"
                        "own_dead,own_hurt,own_severe,enemy_troops,enemy_dead,kp_gained,kp_lost,power,enemy_hurt,enemy_severe,mode) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["mail_id"], r["ts"], day, acc, r["role"], r["parts"], x["foe_name"], x["foe_abbr"],
                         x["foe_castle"], x["side"], x["own_troops"], x["own_dead"], x["own_hurt"], x["own_severe"],
                         x["enemy_troops"], x["enemy_dead"], x["kp_gained"], x["kp_lost"], x["power"],
                         x["enemy_hurt"], x["enemy_severe"], x["mode"]))
            elif r["kind"] == "fort":
                cur = self.c.execute("INSERT INTO forts(mail_id,ts,day,account,level,x,y,pct,tier) VALUES(?,?,?,?,?,?,?,?,?)",
                                     (r["mail_id"], r["ts"], day, acc, r["level"], r["x"], r["y"], r["pct"], r["tier"]))
                lp("f", cur.lastrowid, r["loot"])
            self.c.commit()
            return True

    def q(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    @staticmethod
    def iname(i):
        return (ITEMS.get(i) or {}).get("name", "Item #%d" % i)

    def unknown(self):
        ign, fmap = self.ignored(), None
        items, ignored = [], []
        for r in self.q("SELECT item_id, SUM(qty) qty, COUNT(*) n, MIN(day) first_day, MAX(day) last_day FROM loot "
                        "GROUP BY item_id ORDER BY qty DESC"):
            iid, it = r["item_id"], ITEMS.get(r["item_id"])
            if it and "vermutet" not in it["name"]:
                continue
            fresh = False
            if iid in ign:
                seen = self.ign_counts().get(str(iid))
                if seen is None:
                    self.set_ignored(iid, True)
                elif r["n"] > seen:      # in einem neuen Bericht aufgetaucht: wieder melden
                    self.set_ignored(iid, False)
                    fresh = True
                else:
                    ignored.append({"kind": "item", "id": iid, "qty": r["qty"]})
                    continue
            e = {"id": iid, "qty": r["qty"], "fresh": fresh, "first_day": r["first_day"], "last_day": r["last_day"],
                 "guess": (it or {}).get("name", "")}
            if it:
                e["guess"], e["unit"], e["kind"] = it["name"].replace(" (vermutet)", ""), it["unit"], it["kind"]
            # Fundstelle: der neueste Bericht, in dem das Item vorkam
            s = self.q("SELECT src, src_id, day, account FROM loot WHERE item_id=? ORDER BY day DESC, rowid DESC LIMIT 1", (iid,))
            if s:
                s, ref = s[0], None
                if s["src"] == "b":
                    b = self.q("SELECT mail_id, foe_kind FROM battles WHERE id=?", (s["src_id"],))
                    if b:
                        ref = dict(tab="ft", key="fb%d" % s["src_id"]) if b[0]["foe_kind"] == "fort" \
                            else dict(tab="bt", key="b" + b[0]["mail_id"])
                else:
                    if fmap is None:
                        fmap = {x["rid"]: x["key"] for x in self.fort_entries() if x.get("rid")}
                    if s["src_id"] in fmap:
                        ref = dict(tab="ft", key="f" + fmap[s["src_id"]])
                if ref:
                    ref.update(day=s["day"], account=s["account"])
                    e["ref"] = ref
            items.append(e)
        oth = self.ign_other()
        def split(kind, rows):
            out = []
            for e in rows:
                k = "%s:%s" % (kind, e["id"])
                if k in oth:
                    if e["n"] > oth[k]:      # neuer Bericht: wieder melden
                        self.set_ignored_other(kind, e["id"], False)
                        e["fresh"] = True
                    else:
                        ignored.append({"kind": kind, "id": e["id"], "n": e["n"], "lvl": e.get("lvl")})
                        continue
                out.append(e)
            return out
        heroes = split("hero", [{"id": r["hero1"], "n": r["n"]} for r in self.q(
            "SELECT hero1, COUNT(*) n FROM battles GROUP BY hero1 ORDER BY n DESC") if r["hero1"] not in HEROES])
        foes = split("foe", [{"id": r["foe_hid"], "lvl": r["lvl"], "n": r["n"]} for r in self.q(
            "SELECT foe_hid, MAX(foe_lvl) lvl, COUNT(*) n FROM battles WHERE foe_kind='camp' GROUP BY foe_hid "
            "ORDER BY n DESC") if r["foe_hid"] not in FOES])
        return dict(items=items, heroes=heroes, foes=foes, ignored=ignored)

    def ign_other(self):
        try:
            return json.loads(self.get_setting("ignored_other", "{}"))
        except Exception:
            return {}

    def set_ignored_other(self, kind, iid, flag):
        cur, k = self.ign_other(), "%s:%s" % (kind, iid)
        if flag:
            if kind == "hero":
                n = self.q("SELECT COUNT(*) n FROM battles WHERE hero1=?", (iid,))[0]["n"]
            else:
                n = self.q("SELECT COUNT(*) n FROM battles WHERE foe_kind='camp' AND foe_hid=?", (iid,))[0]["n"]
            cur[k] = n
        else:
            cur.pop(k, None)
        self.set_setting("ignored_other", json.dumps(cur))

    def loot_of(self, src, i):
        ign = self.ignored()
        return [{"id": l["item_id"], "name": self.iname(l["item_id"]), "qty": l["qty"],
                 "unk": l["item_id"] not in ITEMS and l["item_id"] not in ign,
                 "total": l["qty"] * ITEMS[l["item_id"]]["unit"] if l["item_id"] in ITEMS else None,
                 "kind": (ITEMS.get(l["item_id"]) or {}).get("kind")} for l in
                self.q("SELECT item_id, qty FROM loot WHERE src=? AND src_id=?", (src, i))]

    def fort_entries(self, account="", d_from="0000-01-01", d_to="9999-12-31"):
        """Jede Festung = ein Kampfbericht gegen eine Festung (egal ob selbst gestartet oder beigetreten).
        Die Belohnungs-Mail (Level, Position, Anteil, Beute) wird per Zeit und Account zugeordnet.
        Belohnungen ohne passenden Kampfbericht werden ebenfalls aufgeführt."""
        cond, args = "day BETWEEN ? AND ?", [d_from, d_to]
        if account:
            cond += " AND account=?"; args.append(account)
        bs = self.q("SELECT * FROM battles WHERE foe_kind='fort' AND " + cond + " ORDER BY ts", args)
        rs = self.q("SELECT * FROM forts WHERE " + cond + " ORDER BY ts", args)
        used, out = set(), []
        for b in bs:
            best = None
            for r in rs:
                if r["id"] in used or r["account"] != b["account"]:
                    continue
                d = abs(r["ts"] - b["ts"])
                if d <= 300 and (best is None or d < best[0]):
                    best = (d, r)
            r = best[1] if best else None
            if r:
                used.add(r["id"])
            out.append(dict(key="b%d" % b["id"], rid=r["id"] if r else None, ts=b["ts"], day=b["day"], account=b["account"], role=b["role"],
                            parts=b["parts"], hero1=b["hero1"], level=r["level"] if r else None,
                            x=r["x"] if r else None, y=r["y"] if r else None, pct=r["pct"] if r else None,
                            tier=r["tier"] if r else None, loot=self.loot_of("f", r["id"]) if r else []))
        for r in rs:
            if r["id"] not in used:
                out.append(dict(key="f%d" % r["id"], rid=r["id"], ts=r["ts"], day=r["day"], account=r["account"], role=None,
                                parts=None, hero1=None, level=r["level"], x=r["x"], y=r["y"], pct=r["pct"],
                                tier=r["tier"], loot=self.loot_of("f", r["id"])))
        out.sort(key=lambda e: e["ts"], reverse=True)
        return out

    @staticmethod
    def merge_loot(lists):
        m = {}
        for l in lists:
            for x in l:
                e = m.setdefault(x["name"], dict(x, qty=0, total=0 if x["total"] is not None else None))
                e["qty"] += x["qty"]
                if x["total"] is not None:
                    e["total"] = (e["total"] or 0) + x["total"]
        return sorted(m.values(), key=lambda e: -e["qty"])

    def _grouped(self, table, where, a):
        """Die letzten 200 Berichte (Mails) mit all ihren Runden, neueste zuerst."""
        ids = [r["mail_id"] for r in self.q(
            "SELECT mail_id FROM %s WHERE %s GROUP BY mail_id ORDER BY MAX(ts) DESC LIMIT 200" % (table, where), a)]
        if not ids:
            return [], {}
        rows = self.q("SELECT * FROM %s WHERE mail_id IN (%s) ORDER BY id" % (table, ",".join("?" * len(ids))), ids)
        by = {}
        for r in rows:
            by.setdefault(r["mail_id"], []).append(r)
        return ids, by

    @staticmethod
    def _title(names):
        cnt = {}
        for n in names:
            cnt[n] = cnt.get(n, 0) + 1
        return ", ".join(("%d× %s" % (c, n)) if c > 1 else n for n, c in cnt.items())

    def report_battles(self, where, a, fmt):
        ids, by = self._grouped("battles", where, a)
        out = []
        for mid in ids:
            rs = by[mid]
            rounds = [dict(foe="Lvl %d %s" % (b["foe_lvl"], FOES.get(b["foe_hid"], "NPC #%d" % b["foe_hid"])),
                           foe_troops=b["foe_troops"], own_troops=b["own_troops"], hurt=b["hurt"],
                           severe=b["severe"], xp=b["xp"], loot=self.loot_of("b", b["id"])) for b in rs]
            h = rs[0]["hero1"]
            out.append(dict(mail_id=mid, ts=rs[0]["ts"], time=fmt(rs[0]["ts"]), role=rs[0]["role"], parts=rs[0]["parts"],
                            hero=HEROES.get(h, "Kommandant #%d" % h), n=len(rs), title=self._title(r["foe"] for r in rounds),
                            totals={k: sum(r[k] for r in rounds) for k in ("foe_troops", "hurt", "severe", "xp")},
                            rounds=rounds, loot=self.merge_loot(r["loot"] for r in rounds)))
        return out

    PVP_GAP = 300   # Sekunden: Mails desselben Accounts mit weniger Abstand gehören zu einem Kampf-Block

    def pvp_sessions(self, where, a):
        """Fasst zusammengehörende Spielerkampf-Mails (z. B. alle Teile eines Ark-of-Osiris-Kampfs) zusammen."""
        rows = self.q("SELECT * FROM pvp WHERE %s ORDER BY account, ts, id" % where, a)
        out, cur = [], None
        for r in rows:
            if cur and cur["account"] == r["account"] and r["ts"] - cur["last"] <= self.PVP_GAP:
                cur["rows"].append(r); cur["last"] = max(cur["last"], r["ts"])
            else:
                cur = dict(account=r["account"], last=r["ts"], first=r["ts"], rows=[r]); out.append(cur)
        out.sort(key=lambda g: g["last"], reverse=True)
        return out

    @staticmethod
    def pvp_sum(rows):
        keys = ("kp_gained", "kp_lost", "own_severe", "own_hurt", "own_dead", "enemy_severe", "enemy_hurt", "enemy_dead")
        return {k: sum((r[k] or 0) for r in rows if r["role"] == "solo") for k in keys}

    def report_pvp(self, sessions, fmt):
        out = []
        for g in sessions[:200]:
            rs = g["rows"]
            agg = {}
            for r in rs:
                n = ("[%s] " % r["foe_abbr"] if r["foe_abbr"] else "") + r["foe_name"]
                e = agg.setdefault(n, dict(foe=n, n=0, **{k: 0 for k in self.pvp_sum([])}))
                e["n"] += 1
                for k, v in self.pvp_sum([r]).items():
                    e[k] += v
            foes = sorted(agg.values(), key=lambda e: -e["kp_gained"])
            rm = rs[0]["mode"] or ""
            mode = MODES.get(rm, "Spielerkampf (%s)" % rm if rm else "Spielerkampf")
            out.append(dict(key="%s_%d" % (g["account"], g["first"]), ts=g["last"], time=fmt(g["first"]),
                            role=rs[0]["role"], mode=mode, aoo=(rm == "dungeon"), n=len(rs), mails=len({r["mail_id"] for r in rs}), foes=len(foes),
                            title=mode + ": " + foes[0]["foe"] + (" und %d weitere" % (len(foes) - 1) if len(foes) > 1 else ""),
                            totals=self.pvp_sum(rs), rounds=foes))
        return out

    def ignored(self):
        if self._ign is None:
            try:
                self._ign = set(int(x) for x in json.loads(self.get_setting("ignored_items", "[]")))
            except Exception:
                self._ign = set()
        return self._ign

    def ign_counts(self):
        try:
            return json.loads(self.get_setting("ignored_cnt", "{}"))
        except Exception:
            return {}

    def set_ignored(self, iid, flag):
        iid = int(iid)
        cur = set(self.ignored())
        cnt = self.ign_counts()
        if flag:
            cur.add(iid)
            cnt[str(iid)] = self.q("SELECT COUNT(*) n FROM loot WHERE item_id=?", (iid,))[0]["n"]
        else:
            cur.discard(iid)
            cnt.pop(str(iid), None)
        self._ign = cur
        self.set_setting("ignored_items", json.dumps(sorted(cur)))
        self.set_setting("ignored_cnt", json.dumps(cnt))

    def get_setting(self, k, default=""):
        with self.lock:
            r = self.c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_setting(self, k, v):
        with self.lock:
            self.c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))
            self.c.commit()

    def add_profile(self, snaps):
        """Profil-Momentaufnahmen (aus Screenshots abgelesen) speichern; mehrere Dateien pro Tag werden zusammengeführt."""
        n = 0
        with self.lock:
            for s in snaps:
                acc, day = str(s["account"]), str(s["date"])[:10]
                datetime.strptime(day, "%Y-%m-%d")
                data = {k: v for k, v in s.items() if k not in ("account", "date", "name")
                        and isinstance(v, (int, float)) and not isinstance(v, bool)}
                old = self.c.execute("SELECT data, name FROM profile WHERE account=? AND day=?", (acc, day)).fetchone()
                if old:
                    merged = json.loads(old[0]); merged.update(data); data = merged
                self.c.execute("INSERT OR REPLACE INTO profile VALUES(?,?,?,?)",
                               (acc, day, str(s.get("name") or (old[1] if old else "")), json.dumps(data)))
                n += 1
            self.c.commit()
        return n

    def profile_stats(self, d_from, d_to, account=""):
        out = []
        accs = [r["account"] for r in self.q("SELECT DISTINCT account FROM profile" + (" WHERE account=?" if account else ""),
                                              (account,) if account else ())]
        for acc in accs:
            rows = self.q("SELECT day, name, data FROM profile WHERE account=? ORDER BY day", (acc,))
            for r in rows:
                r["data"] = json.loads(r["data"])
            inr = [r for r in rows if d_from <= r["day"] <= d_to]
            if not inr:
                continue
            before = [r for r in rows if r["day"] < d_from]
            base = before[-1] if before else inr[0]
            last = inr[-1]
            delta = {k: last["data"][k] - base["data"][k] for k in last["data"] if k in base["data"]} if base is not last else {}
            out.append(dict(account=acc, name=rows[-1]["name"], base_day=base["day"], last_day=last["day"], delta=delta,
                            last=last["data"], rows=[dict(r["data"], day=r["day"]) for r in reversed(inr)]))
        return out

    def stats(self, d_from, d_to, account=""):
        w, a = "day BETWEEN ? AND ?", [d_from, d_to]
        if account:
            w += " AND account=?"; a.append(account)
        a = tuple(a)
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M:%S")
        solo = lambda col: "COALESCE(SUM(CASE WHEN role='solo' THEN %s END),0)" % col
        wb = w + " AND foe_kind!='fort'"      # Festungen werden getrennt geführt
        tot = self.q("SELECT COUNT(DISTINCT mail_id) battles, COUNT(DISTINCT CASE WHEN role='solo' THEN mail_id END) solo,"
                     " COUNT(DISTINCT CASE WHEN role!='solo' THEN mail_id END) rallies,"
                     " COUNT(CASE WHEN role='solo' THEN 1 END) foes,"
                     + solo("foe_troops") + " killed, COALESCE(SUM(xp),0) xp, " + solo("hurt") + " hurt, "
                     + solo("severe") + " severe FROM battles WHERE " + wb, a)[0]
        forts = self.fort_entries(account, d_from, d_to)
        tot["forts"] = len(forts)
        sess = self.pvp_sessions(w, a)
        isaoo = lambda g: g["rows"][0]["mode"] == "dungeon"
        oth_sess, aoo_sess = [g for g in sess if not isaoo(g)], [g for g in sess if isaoo(g)]
        pv = dict(self.pvp_sum([r for g in oth_sess for r in g["rows"]]), n=len(oth_sess))
        po = dict(self.pvp_sum([r for g in aoo_sess for r in g["rows"]]), n=len(aoo_sess))
        loot = self.q("SELECT item_id, SUM(qty) qty FROM loot WHERE " + w + " GROUP BY item_id ORDER BY qty DESC", a)
        for r in loot:
            it = ITEMS.get(r["item_id"])
            r["name"], r["kind"] = self.iname(r["item_id"]), it["kind"] if it else None
            r["total"] = r["qty"] * it["unit"] if it else None
        L = self.loot_of
        battles = self.report_battles(wb, a, fmt)
        for f in forts:
            f["time"] = fmt(f["ts"])
            f["hero"] = HEROES.get(f["hero1"], "Kommandant #%d" % f["hero1"]) if f["hero1"] is not None else ""
        pvps, aoos = self.report_pvp(oth_sess, fmt), self.report_pvp(aoo_sess, fmt)
        wa, aa = ("WHERE account=?", (account,)) if account else ("", ())
        days = {d["day"]: d for d in self.q(
            "SELECT day, COUNT(DISTINCT CASE WHEN role='solo' THEN mail_id END) solo,"
            " COUNT(DISTINCT CASE WHEN role!='solo' THEN mail_id END) rallies, "
            " COUNT(CASE WHEN role='solo' THEN 1 END) foes, COALESCE(MAX(CASE WHEN role='solo' THEN foe_troops END),0) maxfoe, "
            + solo("foe_troops") + " killed, COALESCE(SUM(xp),0) xp, " + solo("hurt") + " hurt FROM battles WHERE foe_kind!='fort'"
            + (" AND account=?" if account else "") + " GROUP BY day", aa)}
        blank = lambda day: dict(day=day, solo=0, rallies=0, foes=0, maxfoe=0, killed=0, xp=0, hurt=0)
        for g in [g for g in self.pvp_sessions("account=?" if account else "1=1", aa) if not isaoo(g)]:
            dd = datetime.fromtimestamp(g["first"]).strftime("%Y-%m-%d")
            days.setdefault(dd, blank(dd))["pvp"] = days.get(dd, {}).get("pvp", 0) + 1
        for e in self.fort_entries(account):
            days.setdefault(e["day"], blank(e["day"]))["forts"] = days.get(e["day"], {}).get("forts", 0) + 1
        for d in days.values():
            for key in ("forts", "pvp"):
                d.setdefault(key, 0)
        accounts = self.q("SELECT account, MAX(acc_name) acc_name FROM reports WHERE account!='' GROUP BY account ORDER BY MAX(ts) DESC")
        return dict(unknown=self.unknown(), totals=tot, pvp_totals=pv, aoo_totals=po, aoo=aoos, profile=self.profile_stats(d_from, d_to, account), main_account=self.get_setting("main_account"),
                    profile_fields=PROFILE_FIELDS, loot=loot, battles=battles, forts=forts, pvp=pvps,
                    accounts=accounts, days=sorted(days.values(), key=lambda d: d["day"], reverse=True)[:90])

# ---------------------------------------------------------------- Ordner-Überwachung
class Watcher(threading.Thread):
    def __init__(self, folder, db, data_dir):
        super().__init__(daemon=True)
        self.folder, self.db, self.data_dir = folder, db, data_dir
        self.seen, self.fails = db.load_processed(), {}
        self.status = {"files": 0, "imported": 0, "skipped": 0, "errors": 0, "known": len(self.seen),
                       "folder_ok": os.path.isdir(folder), "roots": [], "listing": [], "pending": 0}

    def roots(self):
        """Hauptordner plus, falls vorhanden, die Windows-Umleitung (VirtualStore) für Dateien in Program Files."""
        out = [self.folder]
        local = os.environ.get("LOCALAPPDATA")
        drive, rest = os.path.splitdrive(os.path.abspath(self.folder))
        if local and rest and "program files" in rest.lower():
            vs = os.path.join(local, "VirtualStore", rest.lstrip("\\/"))
            if os.path.isdir(vs):
                out.append(vs)
        return out

    def find_files(self, root):
        """Sucht Mail-Dateien im Ordner und allen Unterordnern."""
        found, stack = [], [root]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif MAIL_NAME.match(e.name):      # z. B. Persistent.Mail.1000006011777373620
                            found.append(e)
            except OSError:
                pass
        return found

    def save_samples(self):
        path = os.path.join(self.data_dir, "unbekannte_berichte.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.db.all_samples(), f, ensure_ascii=False, indent=1)

    def scan(self):
        roots = [r for r in self.roots() if os.path.isdir(r)]
        self.status["folder_ok"] = bool(roots)
        self.status["roots"] = roots
        if not roots:
            return
        try:
            self.status["listing"] = sorted(("[%s]" % e.name if e.is_dir() else e.name) for e in os.scandir(roots[0]))[:15]
        except OSError:
            pass
        entries = []
        for r in roots:
            for e in self.find_files(r):
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime_ns, st.st_size, e))
        entries.sort(key=lambda t: -t[0])          # neueste Mails zuerst, damit frische Berichte sofort erscheinen
        self.status["files"] = len(entries)
        todo = [t for t in entries if self.seen.get(t[2].path) != (t[1], t[0])]
        self.status["pending"] = len(todo)
        for n, (mt, sz, e) in enumerate(todo):
            self.status["pending"] = len(todo) - n
            key = (sz, mt)
            try:
                mail = parse_mail(e.path)
                rep = extract(mail)
            except Exception:
                # Datei wird evtl. gerade vom Spiel geschrieben: erneut versuchen, nach 5 Fehlversuchen aufgeben
                self.status["errors"] += 1
                self.fails[e.path] = self.fails.get(e.path, 0) + 1
                if self.fails[e.path] >= 5:
                    self.seen[e.path] = key
                    self.db.mark_processed(e.path, key[0], key[1], "error")
                continue
            if rep is None:
                # Kein Kampf-/Festungsbericht: ignorieren. Unbekannte Berichte aus dem Reiter REPORT
                # werden nur einmal pro Art als Beispiel abgelegt (für spätere Auswertungen).
                if mail.get("box") in ("Report", "Archive") and self.db.add_sample("%s|Report" % mail.get("type", "?"), sanitize(mail)):
                    self.save_samples()
                self.status["skipped"] += 1
                status = "ignored"
            else:
                if self.db.add(rep):
                    self.status["imported"] += 1
                if rep["kind"] == "battle" and rep["pvp"] and self.db.add_sample("Battle|PvP", sanitize(mail)):
                    self.save_samples()
                status = rep["kind"]
            self.seen[e.path] = key
            self.db.mark_processed(e.path, key[0], key[1], status)
        self.status["pending"] = 0

    def run(self):
        while True:
            self.scan()
            time.sleep(2)

# ---------------------------------------------------------------- Weboberfläche
PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Barbaren-Tracker</title>
<style>
:root{--bg:#090d15;--card:#0f1623;--soft:#141d2e;--ink:#e8edf6;--mute:#8793a8;--line:#1f2b42;--dash:#31425f;--acc:#3b82f6;--accsoft:rgba(59,130,246,.16);--good:#34d399;--bad:#fb7185;--warn:#fbbf24;color-scheme:dark}
@media(prefers-color-scheme:light){:root{--bg:#eef2f8;--card:#fff;--soft:#f2f5fb;--ink:#14213a;--mute:#5b6b86;--line:#dbe3f0;--dash:#c3cfe3;--acc:#2563eb;--accsoft:rgba(37,99,235,.10);--good:#0f9f6e;--bad:#d1405a;--warn:#b45309;color-scheme:light}}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font:15px/1.5 Inter,"Segoe UI",system-ui,sans-serif;background:radial-gradient(900px 420px at 12% -8%,rgba(59,130,246,.18),transparent 62%),radial-gradient(720px 360px at 98% 0,rgba(52,211,153,.09),transparent 60%),var(--bg) no-repeat}
main{max-width:1160px;margin:0 auto;padding:22px 16px 70px}
.hero{display:grid;grid-template-columns:auto 1fr;gap:20px;padding:22px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(135deg,var(--soft),var(--card));margin-bottom:14px;box-shadow:0 18px 40px -26px rgba(0,0,0,.7)}
.av{width:88px;height:88px;border-radius:50%;padding:3px;background:conic-gradient(from 210deg,#3b82f6,#34d399,#fbbf24,#3b82f6)}
.av span{display:grid;place-items:center;width:100%;height:100%;border-radius:50%;background:var(--card);font-weight:800;font-size:1.6rem;letter-spacing:.02em}
.hrow{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;justify-content:space-between}
h1{font-size:1.7rem;line-height:1.15;margin:0;letter-spacing:-.01em}h2{font-size:1.05rem;margin:0;display:inline}
#per{margin:4px 0 0;color:var(--ink)}#st{font-size:.82rem;color:var(--mute)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,input,button{font:inherit;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:7px 12px}
button{cursor:pointer;transition:border-color .15s,background .15s}button:hover{border-color:var(--acc)}
select option,select optgroup{background:var(--card);color:var(--ink)}
select option:checked,select option:hover{background:var(--acc);color:#fff}
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.chip{border:1px dashed var(--dash);border-radius:12px;padding:8px 14px;min-width:132px;background:rgba(127,127,127,.04)}
.chip b{display:block;font-size:1.15rem;font-variant-numeric:tabular-nums}.chip span{color:var(--mute);font-size:.82rem}.chip em{font-style:normal;font-weight:600;margin-left:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(214px,1fr));gap:12px;margin:12px 0}
.k{border:1px dashed var(--dash);border-radius:14px;padding:12px 16px;background:rgba(127,127,127,.04)}
.k b{display:block;font-size:1.55rem;line-height:1.2;font-variant-numeric:tabular-nums}.k span{display:block;color:var(--mute);font-size:.84rem}.k span.good{color:var(--good)}.k span.bad{color:var(--bad)}
.kc{display:grid;grid-template-columns:auto 1fr;column-gap:12px;align-items:center}
.kc .ic{grid-row:1/3;display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:var(--accsoft);font-style:normal;font-size:1.25rem}
.kc b{font-size:1.4rem}.kc span{overflow-wrap:anywhere}
.cnt{color:var(--mute);font-size:.82rem}.cnt:empty{display:none}
.loot{display:grid;grid-template-columns:repeat(auto-fill,minmax(236px,1fr));gap:12px}
.l{display:grid;grid-template-columns:52px 1fr;column-gap:12px;align-items:center;border:1px solid var(--line);background:var(--soft);border-radius:14px;padding:12px 14px}
.l i{grid-row:1/3;display:grid;place-items:center;width:52px;height:52px;border-radius:12px;background:var(--accsoft);font-style:normal;font-size:1.7rem}
.l{position:relative}
.l[data-tip]:hover::after,.l[data-tip]:focus-visible::after{content:attr(data-tip);position:absolute;left:12px;right:12px;top:calc(100% - 4px);z-index:5;padding:9px 12px;border-radius:10px;background:#060a12;color:#e8edf6;border:1px solid var(--dash);font-size:.82rem;line-height:1.45;box-shadow:0 14px 30px -10px rgba(0,0,0,.8);pointer-events:none}
.mi{width:20px;height:20px;object-fit:contain;vertical-align:middle;flex:none}.ch{display:inline-flex;align-items:center;gap:5px;margin-right:14px;white-space:nowrap}
.ll{list-style:none;padding:0}.ll li{display:flex;align-items:center;gap:8px;margin:5px 0}.ll .mi{width:28px;height:28px}
.l i img{width:100%;height:100%;object-fit:contain;border-radius:8px}.l b{font-size:1.2rem;font-variant-numeric:tabular-nums}.l small{color:var(--mute)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th.t,td.t{text-align:left}th{color:var(--mute);font-weight:600;font-size:.82rem}
tr:hover td{background:var(--soft)}
.bad{color:var(--bad)}.good{color:var(--good)}.note{color:var(--mute);font-size:.85rem}
td.lt{white-space:normal;text-align:left;color:var(--mute);font-size:.85rem}
.tag{display:inline-block;padding:0 10px;border-radius:999px;border:1px solid var(--line);font-size:.8rem;white-space:nowrap}
.tag.r{border-color:var(--acc);color:var(--acc);background:var(--accsoft)}
summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:10px}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";color:var(--mute);width:1em;flex:none;transition:transform .12s}
details[open]>summary::before{transform:rotate(90deg)}
details.rep{border:1px solid var(--line);border-radius:14px;margin-bottom:9px;background:var(--soft)}
details.rep[open]{border-color:var(--dash)}
details.rep>summary{padding:11px 16px;flex-wrap:wrap;row-gap:2px;border-radius:14px}
details.rep>summary:hover{background:var(--accsoft)}
.tm{color:var(--mute);font-variant-numeric:tabular-nums;min-width:8.5em}
.sm{color:var(--mute);font-size:.85rem;flex:1 1 220px}
.rb{padding:6px 16px 16px 40px}
.fa{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 12px}
.fa div{border:1px dashed var(--dash);border-radius:10px;padding:7px 12px}.fa dt{color:var(--mute);font-size:.8rem}.fa dd{margin:0;font-weight:700;font-variant-numeric:tabular-nums}
.rb h3,#pf h3{font-size:.95rem;margin:12px 0 6px}.ll{margin:0}
[hidden]{display:none!important}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:6px;margin-top:6px;border:1px solid var(--line);border-radius:16px;background:var(--card)}
.tabs button{border:0;border-radius:11px;background:none;color:var(--mute);padding:9px 16px;white-space:nowrap;font-weight:600}
.tabs button:hover{background:var(--soft);color:var(--ink)}
.tabs button.on{background:var(--acc);color:#fff;box-shadow:0 8px 20px -8px var(--acc)}
.tabs .cnt{margin-left:6px;padding:1px 8px;border-radius:999px;background:var(--soft)}.tabs .on .cnt{background:rgba(255,255,255,.24);color:#fff}
.pane{margin-top:12px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px}
[data-item]{cursor:pointer}
#nm td{white-space:normal}#nm td.t:last-child{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.ch.unk{padding:1px 9px;border-radius:999px;border:1px solid var(--warn);background:rgba(251,191,36,.13);color:var(--warn)}
.ll li.unk{padding:4px 10px;border-radius:10px;border:1px dashed var(--warn);background:rgba(251,191,36,.10);color:var(--warn)}
.qm{display:inline-grid;place-items:center;width:17px;height:17px;border-radius:50%;background:var(--warn);color:#111;font-size:.72rem}
.ll li.unk small{margin-left:auto;font-size:.78rem}
.nmbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:6px 0 10px}
tr.flash td,details.flash{animation:fl 2.6s ease-out}
@keyframes fl{0%{box-shadow:0 0 0 3px var(--acc);background:var(--accsoft)}100%{box-shadow:0 0 0 0 transparent}}
@media(prefers-reduced-motion:reduce){tr.flash td,details.flash{animation:none;outline:2px solid var(--acc)}}
@media(max-width:640px){.grid{grid-template-columns:1fr 1fr}.kc{grid-template-columns:1fr}.kc .ic{display:none}.hero{grid-template-columns:1fr}.av{width:64px;height:64px}.av span{font-size:1.2rem}.rb{padding-left:16px}}
</style></head><body><main>
<section class="hero"><div class="av"><span id="hav">⚔</span></div><div>
<div class="hrow"><div><h1 id="hname">Barbaren-Tracker</h1><div id="per"></div><div id="st"></div></div>
<div class="bar"><select id="acc" hidden></select><button id="mainbtn" hidden></button><select id="range">
<option value="today">Heute</option><option value="yesterday">Gestern</option><option value="7">Letzte 7 Tage</option>
<option value="30">Letzte 30 Tage</option><option value="month">Dieser Monat</option><option value="all">Alles</option>
<option value="custom">Zeitraum wählen</option></select>
<span id="cu" hidden><label>von <input type="date" id="d1"></label> <label>bis <input type="date" id="d2"></label></span>
<button id="expand">Alle Berichte aufklappen</button><button id="collapse">zuklappen</button><button id="lang" title="Language / Sprache">English</button></div></div>
<div class="chips" id="hchips"></div></div></section>
<div class="grid" id="kpi"></div>
<p class="note" id="kpinote"></p>
<nav class="tabs" id="tabs" role="tablist">
<button role="tab" data-t="loot">Beute</button>
<button role="tab" data-t="ft">Festungen <span class="cnt" id="c_ft"></span></button>
<button role="tab" data-t="bt">Barbarenkämpfe <span class="cnt" id="c_bt"></span></button>
<button role="tab" data-t="pv" id="pvsec">Spielerkämpfe <span class="cnt" id="c_pv"></span></button>
<button role="tab" data-t="ao" id="aosec">Ark of Osiris <span class="cnt" id="c_ao"></span></button>
<button role="tab" data-t="pf">Profil</button>
<button role="tab" data-t="dt">Pro Tag</button>
<button role="tab" data-t="nm" id="nmsec">Namen eintragen <span class="cnt" id="c_nm"></span></button>
</nav>
<div class="pane" id="p_loot"><div class="loot" id="loot"></div><p class="note" id="lootnote"></p></div>
<div class="pane" id="p_ft"><div id="ft"></div></div>
<div class="pane" id="p_bt"><div id="bt"></div></div>
<div class="pane" id="p_pv"><p class="note">Zusammengehörende Berichte stehen in einer Zeile. Ark of Osiris steht getrennt im eigenen Reiter und ist hier nicht mitgezählt.</p><div id="pv"></div></div>
<div class="pane" id="p_ao"><p class="note">Ark of Osiris, getrennt von den anderen Spielerkämpfen gezählt.</p><div class="grid" id="aokpi"></div><div id="ao"></div></div>
<div class="pane" id="p_pf"><p class="note">Profilwerte aus deinen Screenshots. Die Datei, die ich dir schicke, fügst du hier ein. Der Verlauf wird gespeichert und nichts geht beim erneuten Einfügen doppelt verloren.</p>
<p><button id="pbtn">Profil-Datei einfügen …</button> <input type="file" id="pfile" accept=".json" multiple hidden> <span class="note" id="pfmsg"></span></p><div id="pf"></div></div>
<div class="pane" id="p_dt"><div class="wrap"><table id="dt"></table></div></div>
<div class="pane" id="p_nm"><p class="note">Diese Nummern kennt das Programm noch nicht. Schau im Spiel nach (Tooltip auf das Item, Kommandant im Bericht) und trage den Namen ein. Gilt sofort auch für alle alten Berichte.</p>
<div class="nmbar"><button id="ignall" title="Alle offenen Namen ignorieren. Sobald einer in einem neuen Bericht auftaucht, erscheint er wieder.">Alle ignorieren</button> <label>Sortieren nach <select id="nmsort"><option value="qty">Häufigkeit</option><option value="new">Datum: zuletzt gesehen (neueste zuerst)</option><option value="old">Datum: zuerst gesehen (älteste zuerst)</option></select></label>
<span class="note">Unbenannte Items erscheinen in den Berichten gelb markiert. Ein Klick darauf führt hierher, und "Zum Bericht" führt zurück. Ignorierte Namen erscheinen wieder, sobald sie in einem neuen Bericht vorkommen (Markierung "neu").</span></div>
<div class="wrap"><table id="nm"></table></div><div id="nmign"></div></div>
</main><script>
let LANG='de';try{LANG=localStorage.getItem('roklang')==='en'?'en':'de'}catch(e){}
const LOC=LANG==='en'?'en-US':'de-DE',$=id=>document.getElementById(id),f=n=>Number(n).toLocaleString(LOC);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const iso=d=>{const z=new Date(d-d.getTimezoneOffset()*6e4);return z.toISOString().slice(0,10)};
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dm=d=>LANG==='en'?(p=>p.length===3?+p[2]+' '+MON[+p[1]-1]+' '+p[0]:d)(d.split('-')):d.split('-').reverse().join('.');
let saved={};try{saved=JSON.parse(localStorage.getItem('rokbarbs')||'{}')}catch(e){}
if(saved.range)$('range').value=saved.range;if(saved.d1)$('d1').value=saved.d1;if(saved.d2)$('d2').value=saved.d2;
// Einzelne Berichte: aufgeklappt bleiben sie auch beim automatischen Aktualisieren offen
const openRep=new Set();
document.addEventListener('toggle',e=>{const d=e.target;if(d instanceof HTMLDetailsElement&&d.dataset.k)d.open?openRep.add(d.dataset.k):openRep.delete(d.dataset.k)},true);
// Reiter
let tab='loot';try{tab=localStorage.getItem('roktab')||'loot'}catch(e){}
function showTab(t){const btn=document.querySelector(`#tabs [data-t="${t}"]`);if(!btn||btn.hidden)t='loot';tab=t;
 document.querySelectorAll('#tabs button').forEach(b=>{const on=b.dataset.t===t;b.classList.toggle('on',on);b.setAttribute('aria-selected',on)});
 document.querySelectorAll('.pane').forEach(p=>p.hidden=p.id!=='p_'+t);
 try{localStorage.setItem('roktab',t)}catch(e){}}
$('tabs').onclick=e=>{const b=e.target.closest('button');if(b)showTab(b.dataset.t)};showTab(tab);
$('expand').onclick=()=>document.querySelectorAll('details.rep').forEach(d=>d.open=true);
$('collapse').onclick=()=>document.querySelectorAll('details.rep').forEach(d=>d.open=false);
function range(){const v=$('range').value,t=new Date();$('cu').hidden=v!=='custom';
 if(v==='today')return[iso(t),iso(t)];
 if(v==='yesterday'){t.setDate(t.getDate()-1);return[iso(t),iso(t)]}
 if(v==='all')return['0000-01-01','9999-12-31'];
 if(v==='month')return[iso(new Date(t.getFullYear(),t.getMonth(),1)),iso(t)];
 if(v==='custom'){let x=$('d1').value||iso(t),y=$('d2').value||x;if(x>y)[x,y]=[y,x];return[x,y]}
 const s=new Date();s.setDate(s.getDate()-(+v-1));return[iso(s),iso(t)]}
const trade=(g,l)=>l>0?Math.round(g/l*100):(g>0?null:0);   // 100 % = ausgeglichener Trade (Gegner verliert so viel wie du)
const tr=(g,l)=>{const v=trade(g,l);return v===null?'∞':v.toLocaleString(LOC)+' %'};
const trc=(g,l)=>{const v=trade(g,l);return v===null||v>=100?'good':'bad'};
const LI={'Training-Speedups':'⏩','Universal-Speedups':'⏱️','Heilungs-Speedups':'💊','Holz':'🪵','Stein':'🪨','Gold':'🪙','Edelsteine':'💎','XP aus Büchern':'📘','Ressourcenpakete':'📦','Aktionspunkte (Notfall)':'⚡','Mais':'🌽'};
const slug=t=>String(t).normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
function icoFb(img,alt,emoji){if(alt&&!img.dataset.t){img.dataset.t=1;img.src='/icon/'+alt}else img.replaceWith(document.createTextNode(emoji))}
const TIPS={speed_training:'Beschleunigt die Ausbildung und Aufwertung von Truppen.',speed_universal:'Universal-Beschleunigung: gilt für Bau, Forschung, Ausbildung und Heilung.',
 speed_healing:'Beschleunigt die Heilung verwundeter Truppen.',food:'Mais: Rohstoff für Truppen und Gebäude.',wood:'Holz: Rohstoff für Gebäude, Forschung und Truppen.',
 stone:'Stein: Rohstoff für Gebäude, Forschung und Truppen.',gold:'Gold: Rohstoff für Gebäude, Forschung und Truppen.',gems:'Edelsteine: Premium-Währung.',
 xp_books:'Gibt einem Kommandanten Erfahrung (XP).',packs:'Ressourcenpaket. Lvl 2: zufällig 10.000 Mais, 10.000 Holz, 7.500 Stein oder 5.000 Gold (aus Barbarenfestungen, Hungry-Turkey-Event). Lvl 1 (C): zufällig 1.000 Mais, 1.000 Holz, 750 Stein oder 500 Gold.',
 ap:'Notfall-Aktionspunkte: stellen sofort Aktionspunkte für Angriffe auf Barbaren wieder her.'};
const ICONS=[[/Trade/,'📈'],[/Kill Count/,'☠️'],[/Kill Points/,'⚔️'],[/[Ss]chwer/,'🩸'],[/eicht/,'🩹'],[/Tote/,'💀'],[/XP/,'⭐'],[/Festung/,'🏰'],[/Rally|Rallys/,'🛡️'],[/Gegner besiegt/,'🎯'],[/Barbar/,'🪓'],[/Solo/,'🧍'],[/ämpfe/,'🗡️']];
const kpi=(l,v,c)=>`<div class="k kc"><i class="ic">${(ICONS.find(x=>x[0].test(l))||[0,'◆'])[1]}</i><b class="${c||''}">${v}</b><span>${l}</span></div>`;
const role={solo:'<span class="tag">Solo</span>',host:'<span class="tag r">Rally gestartet</span>',joined:'<span class="tag r">Rally beigetreten</span>'};
const SUF={speed_training:' Min',speed_universal:' Min',speed_healing:' Min',ap:' AP'};
const lt=l=>l.map(x=>x.qty+'× '+esc(x.name)).join(', ');
const IK=new Set(['speed_training','speed_universal','speed_healing','wood','stone','gold','food','gems','xp_books','packs','ap']);
const EMO={speed_training:'⏩',speed_universal:'⏱️',speed_healing:'💊',wood:'🪵',stone:'🪨',gold:'🪙',food:'🌽',gems:'💎',xp_books:'📘',packs:'📦',ap:'⚡'};
const mi=x=>`<img class="mi" src="/icon/${IK.has(x.kind)?x.kind:'item_'+x.id}" alt="" onerror="icoFb(this,'${slug(x.name)}','${EMO[x.kind]||'🎁'}')">`;
const chipsH=l=>l.length?l.slice().sort((a,b)=>(b.unk?1:0)-(a.unk?1:0)).slice(0,4).map(x=>`<span class="ch${x.unk?' unk':''}"${x.unk?` data-item="${x.id}" title="Noch nicht benannt. Klicken zum Benennen."`:''}>${mi(x)}${f(x.qty)}× ${esc(x.name)}${x.unk?' <b class="qm">?</b>':''}</span>`).join('')+(l.length>4?`<span class="ch">+${l.length-4} weitere</span>`:''):'keine Beute';
const chips=l=>l.length?l.slice(0,3).map(x=>f(x.qty)+'× '+x.name).join(', ')+(l.length>3?` (+${l.length-3} weitere)`:''):'keine Beute';
const lootList=l=>l.length?`<ul class="ll">${l.map(x=>`<li${x.unk?` class="unk" data-item="${x.id}" title="Noch nicht benannt. Klicken zum Benennen."`:''}>${mi(x)}<b>${f(x.qty)}×</b> ${esc(x.name)}${x.total!=null&&x.total!==x.qty?` <span class="note">= ${f(x.total)}${SUF[x.kind]||''}</span>`:''}${x.unk?' <b class="qm">?</b><small>noch nicht benannt</small>':''}</li>`).join('')}</ul>`:'<p class="note">Keine Beute in diesem Bericht.</p>';
const facts=a=>`<dl class="fa">${a.map(([k,v,c])=>`<div><dt>${k}</dt><dd class="${c||''}">${v}</dd></div>`).join('')}</dl>`;
const rep=(k,head,body)=>`<details class="rep" data-k="${k}"${openRep.has(k)?' open':''}><summary>${head}</summary><div class="rb">${body}</div></details>`;
const tbl=(h,rows)=>`<div class="wrap"><table><tr>${h.map(x=>`<th>${x}</th>`).join('')}</tr>${rows.join('')}</table></div>`;
const none=t=>`<p class="note">${t}</p>`;
const empty=(n,t)=>`<tr><td colspan="${n}" class="note">${t}</td></tr>`;
const KINDS=[['speed_training','Training-Speedup (Min)'],['speed_universal','Universal-Speedup (Min)'],['speed_healing','Heilungs-Speedup (Min)'],['wood','Holz'],['stone','Stein'],['gold','Gold'],['food','Mais'],['gems','Edelsteine'],['xp_books','XP-Buch'],['packs','Ressourcenpaket'],['ap','Aktionspunkte'],['other','Sonstiges']];
let nmSig='',lastSig='';
let nmSort='qty';try{nmSort=localStorage.getItem('roknmsort')||'qty'}catch(e){}$('nmsort').value=nmSort;
$('ignall').onclick=async()=>{const all=[...lastNames.items.map(x=>['item',x.id]),...lastNames.heroes.map(x=>['hero',x.id]),...lastNames.foes.map(x=>['foe',x.id])];if(!all.length)return;for(const [kind,id] of all)await fetch('/api/ignore',{method:'POST',body:JSON.stringify({id,kind,ignore:true})});nmSig='';lastSig='';load()};
$('nmsort').onchange=()=>{nmSort=$('nmsort').value;try{localStorage.setItem('roknmsort',nmSort)}catch(e){}nmSig='';load()};
let lastNames={items:[]};
function renderNames(u){lastNames=u;const sig=JSON.stringify([u,nmSort]);if(sig===nmSig)return;nmSig=sig;
 const n=u.items.length+u.heroes.length+u.foes.length;$('nmsec').hidden=!n&&!u.ignored.length;$('c_nm').textContent=n?`${n} offen`:'';
 const ks=k=>KINDS.map(x=>`<option value="${x[0]}"${x[0]===k?' selected':''}>${x[1]}</option>`).join('');
 const row=(kind,id,label,extra)=>`<tr data-kind="${kind}" data-id="${id}"><td class="t">${label}</td><td class="t"><input class="n" placeholder="Name" value="${esc(extra.guess)}"> ${extra.item?`<input class="u" type="number" step="any" style="width:90px" placeholder="Wert/Stück" value="${extra.unit??''}" title="Wert pro Stück, z. B. 5 für 5 Min oder 1000 für 1.000 Holz"> <select class="k">${ks(extra.kind||'other')}</select>`:''} <button class="sv">Speichern</button>${extra.ref?` <button class="go" data-ref='${esc(JSON.stringify(extra.ref))}' title="Neuester Bericht mit diesem Item">Zum Bericht</button>`:''}<button class="ig" title="Vorerst ignorieren: nicht in der Liste offener Namen. Taucht er in einem neuen Bericht auf, erscheint er wieder.">Ignorieren</button> <span class="note"></span></td></tr>`;
 const items=u.items.slice().sort((a,b)=>nmSort==='new'?(b.last_day.localeCompare(a.last_day)||b.qty-a.qty):nmSort==='old'?(a.first_day.localeCompare(b.first_day)||b.qty-a.qty):b.qty-a.qty);
 $('nm').innerHTML=items.map(x=>row('item',x.id,`Item #${x.id}${x.fresh?' <span class="cnt">neu</span>':''}<br><small>${f(x.qty)}× gefunden · ${x.first_day===x.last_day?dm(x.last_day):dm(x.first_day)+' bis '+dm(x.last_day)}</small>`,{item:1,guess:x.guess,unit:x.unit,kind:x.kind,ref:x.ref})).join('')+
  u.heroes.map(x=>row('hero',x.id,`Kommandant #${x.id}${x.fresh?' <span class="cnt">neu</span>':''}<br><small>${f(x.n)} Kämpfe</small>`,{})).join('')+
  u.foes.map(x=>row('foe',x.id,`Gegner #${x.id}${x.fresh?' <span class="cnt">neu</span>':''} (Lvl ${x.lvl})<br><small>${f(x.n)} Kämpfe</small>`,{})).join('');
 $('nmign').innerHTML=u.ignored.length?`<details class="rep" style="margin-top:14px"><summary><b>Ignoriert</b> <span class="cnt">${u.ignored.length}</span></summary><div class="rb">${u.ignored.map(x=>`<div style="margin:6px 0">${x.kind==='hero'?'Kommandant':x.kind==='foe'?'Gegner':'Item'} #${x.id} <span class="note">${x.kind==='item'?f(x.qty)+'× gefunden':f(x.n)+' Kämpfe'}</span> <button class="un" data-id="${x.id}" data-kind="${x.kind}">Wieder anzeigen</button></div>`).join('')}</div></details>`:''}
function flash(el){el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');el.scrollIntoView({block:'center',behavior:'smooth'});setTimeout(()=>el.classList.remove('flash'),2800)}
function goItem(id){showTab('nm');const tr=document.querySelector(`#nm tr[data-kind="item"][data-id="${id}"]`);if(tr)flash(tr)}
async function goReport(ref){
 if(ref.account&&$('acc').value&&$('acc').value!==ref.account&&[...$('acc').options].some(o=>o.value===ref.account))$('acc').value=ref.account;
 $('range').value='custom';$('d1').value=ref.day;$('d2').value=ref.day;lastSig='';await load();showTab(ref.tab);
 const d=document.querySelector(`details.rep[data-k="${ref.key}"]`);if(d){d.open=true;flash(d)}}
document.addEventListener('click',e=>{const t=e.target.closest('[data-item]');if(!t)return;e.preventDefault();e.stopPropagation();goItem(t.dataset.item)},true);
$('p_nm').addEventListener('click',async e=>{const btn=e.target.closest('button');if(!btn)return;
 if(btn.classList.contains('un')){await fetch('/api/ignore',{method:'POST',body:JSON.stringify({id:btn.dataset.id,kind:btn.dataset.kind,ignore:false})});nmSig='';lastSig='';return load()}
 const tr=btn.closest('tr');if(!tr)return;const q=c=>tr.querySelector(c);
 if(btn.classList.contains('go'))return goReport(JSON.parse(btn.dataset.ref));
 if(btn.classList.contains('ig')){await fetch('/api/ignore',{method:'POST',body:JSON.stringify({id:tr.dataset.id,kind:tr.dataset.kind,ignore:true})});nmSig='';lastSig='';return load()}
 const body={kind:tr.dataset.kind,id:tr.dataset.id,name:q('.n').value};if(q('.u')){body.unit=q('.u').value;body.ikind=q('.k').value}
 const r=await fetch('/api/name',{method:'POST',body:JSON.stringify(body)}).then(r=>r.json());
 q('span').textContent=r.ok?'gespeichert':'Fehler: '+r.error;if(r.ok){nmSig='';lastSig='';load()}});
let mainApplied=false,lastMain='';
async function load(){const[a,b]=range();
 localStorage.setItem('rokbarbs',JSON.stringify({range:$('range').value,d1:$('d1').value,d2:$('d2').value}));
 const [s,st]=await Promise.all([fetch(`/api/stats?from=${a}&to=${b}&account=${$('acc').value}`).then(r=>r.json()),fetch('/api/status').then(r=>r.json())]);
 const sig=JSON.stringify([s,st,a,b]);if(sig===lastSig)return;lastSig=sig;
 const cur=$('acc').value;
 if(s.accounts.length>1&&$('acc').options.length!==s.accounts.length+1){$('acc').innerHTML='<option value="">Alle Accounts</option>'+s.accounts.map(x=>`<option value="${x.account}">${esc(x.acc_name||x.account)}</option>`).join('');$('acc').value=cur;$('acc').hidden=false}
 if(!mainApplied){mainApplied=true;if(s.main_account&&s.accounts.some(x=>x.account===s.main_account)&&$('acc').value!==s.main_account){$('acc').value=s.main_account;lastSig='';return load()}}
 lastMain=s.main_account||'';$('mainbtn').hidden=s.accounts.length<2;const isMain=!!$('acc').value&&$('acc').value===s.main_account;
 $('mainbtn').textContent=isMain?'★ Haupt-Account':'☆ Als Haupt-Account';$('mainbtn').disabled=!$('acc').value;
 $('mainbtn').title=$('acc').value?(isMain?'Haupt-Account wieder entfernen':'Beim Start immer diesen Account anzeigen'):'Wähle zuerst einen einzelnen Account aus';
 $('per').innerHTML=a.startsWith('0000')?'<b>Gesamter Zeitraum</b>':`<b>${dm(a)}${a===b?'':' bis '+dm(b)}</b>`;
 $('st').textContent=!st.folder_ok?'Ordner nicht gefunden. Pfad mit --folder angeben.':st.files===0?
  `Keine Berichts-Dateien gefunden in: ${st.roots.join(' | ')}. Inhalt: ${st.listing.length?st.listing.join(', '):'(leer)'}`:
  `Überwache ${st.files} Dateien (${st.imported} neu seit Start)`+(st.pending?` · Lese gerade noch ${st.pending} Dateien, neueste zuerst …`:'');
 const t=s.totals,pt=s.pvp_totals,po=s.aoo_totals;
 $('kpi').innerHTML=kpi('Solo-Berichte',f(t.solo))+kpi('Rallys',f(t.rallies))+kpi('Festungen zerstört',f(t.forts))+
  kpi('Gegner besiegt (solo)',f(t.foes))+kpi('Barbaren-Truppen besiegt (solo)',f(t.killed))+kpi('Kommandanten-XP',f(t.xp))+
  kpi('Leicht verwundet (solo)',f(t.hurt))+kpi('Schwer verwundet (solo)',f(t.severe),'bad')+
  (pt.n?kpi('Spielerkämpfe',f(pt.n))+kpi('Kill Points',f(pt.kp_gained))+kpi('Kill Count (Gegner schwer verwundet)',f(pt.enemy_severe))+kpi('Eigene schwer verwundet',f(pt.own_severe),'bad')+kpi('Trade (schwer verwundet)',tr(pt.enemy_severe,pt.own_severe),trc(pt.enemy_severe,pt.own_severe)):'');
 $('kpinote').textContent='Barbaren-Truppen = Summe der Truppenstärke aller besiegten Gegner (nicht die Anzahl). Zahlen zu Truppen und Verlusten aus Rallys gelten für alle Teilnehmer zusammen und sind hier nicht eingerechnet.';
 const sum=k=>s.loot.filter(x=>x.kind===k).reduce((n,x)=>n+x.total,0);
 const big=[['Training-Speedups',sum('speed_training'),'Min','speed_training'],['Universal-Speedups',sum('speed_universal'),'Min','speed_universal'],['Heilungs-Speedups',sum('speed_healing'),'Min','speed_healing'],
  ['Holz',sum('wood'),'','wood'],['Stein',sum('stone'),'','stone'],['Gold',sum('gold'),'','gold'],['Edelsteine',sum('gems'),'','gems'],['XP aus Büchern',sum('xp_books'),'','xp_books'],
  ['Ressourcenpakete',sum('packs'),'Stück','packs'],['Aktionspunkte (Notfall)',sum('ap'),'AP','ap'],['Mais',sum('food'),'','food']]
  .concat(s.loot.filter(x=>x.kind==='other').map(x=>[x.name,x.qty,'×','item_'+x.item_id])).filter(x=>x[1]>0);
 $('loot').innerHTML=big.length?big.map(x=>`<div class="l" tabindex="0" data-tip="${esc(TIPS[x[3]]||x[0])}"><i><img src="/icon/${x[3]}" alt="" onerror="icoFb(this,'${slug(x[0])}','${LI[x[0]]||'🎁'}')"></i><b>${f(x[1])} ${x[2]}</b><small>${esc(x[0])}</small></div>`).join(''):'<span class="note">Keine Beute im gewählten Zeitraum.</span>';
 const ignIds=new Set(s.unknown.ignored.map(x=>x.id)),unk=s.loot.filter(x=>x.kind===null&&!ignIds.has(x.item_id));
 renderNames(s.unknown);
 $('lootnote').textContent=unk.length?'Noch nicht benannt: '+unk.map(x=>`${x.name} (${f(x.qty)}×)`).join(', '):'';
 // Festungen (jeder Festungs-Kampf, auch beigetretene Rallys)
 $('c_ft').textContent=s.forts.length||'';
 $('ft').innerHTML=s.forts.map(x=>rep('f'+x.key,
   `<span class="tm">${x.time}</span>${x.role?role[x.role]:''}<b>Barbarenfestung${x.level?' Lvl '+x.level:''}</b><span class="note">${x.pct==null?'':'Anteil '+x.pct+' %'}${x.tier==null?'':', Tier '+x.tier}</span><span class="sm">${chipsH(x.loot)}</span>`,
   facts([['Position',x.x==null?'–':`${x.x} / ${x.y}`],['Dein Schadensanteil',x.pct==null?'–':x.pct+' %'],['Tier',x.tier==null?'–':x.tier],['Teilnehmer',x.parts||'–'],['Kommandant',esc(x.hero)||'–']])+
   (x.level==null?'<p class="note">Zu dieser Festung gibt es keine Belohnungs-Mail im Ordner, deshalb fehlen Level, Position und Beute.</p>':'')+
   '<h3>Das hast du bekommen</h3>'+lootList(x.loot))).join('')||none('Keine Festungen im gewählten Zeitraum.');
 // Barbarenkämpfe (ein Bericht = eine Zeile, die Runden/Gegner stehen darin)
 $('c_bt').textContent=s.battles.length?(s.battles.length>=200?'letzte 200':s.battles.length):'';
 $('bt').innerHTML=s.battles.map(x=>{const so=x.role==='solo',t=x.totals;
  return rep('b'+x.mail_id,
   `<span class="tm">${x.time}</span>${role[x.role]}<b>${esc(x.title)}</b><span class="note">${esc(x.hero)}</span><span class="sm">${chipsH(x.loot)}</span>`,
   facts(so?[['Gegner im Bericht',x.n],['Gegner-Truppen',f(t.foe_troops)],['Leicht verwundet',f(t.hurt)],['Schwer verwundet',f(t.severe),'bad'],['Kommandanten-XP',f(t.xp)]]
           :[['Teilnehmer',x.parts],['Gegner im Bericht',x.n],['Kommandanten-XP',f(t.xp)]])+
   (so?(x.n>1?'<h3>Die einzelnen Gegner</h3>'+tbl(['Gegner','Gegner-Truppen','Eigene Truppen','Leicht verwundet','Schwer verwundet','XP','Beute'],
        x.rounds.map(r=>`<tr><td>${esc(r.foe)}</td><td>${f(r.foe_troops)}</td><td>${f(r.own_troops)}</td><td>${f(r.hurt)}</td><td class="bad">${f(r.severe)}</td><td>${f(r.xp)}</td><td class="lt">${chipsH(r.loot)}</td></tr>`)):'')
      :'<p class="note">Truppen und Verluste gelten bei Rallys für alle Teilnehmer zusammen und werden nicht einzeln angezeigt.</p>')+
   '<h3>Das hast du bekommen</h3>'+lootList(x.loot))}).join('')||none('Keine Kämpfe im gewählten Zeitraum.');
 // Spielerkämpfe (ein Bericht = eine Zeile, alle Runden/Gegner darin)
const pvList=l=>l.map(x=>{const so=x.role==='solo',t=x.totals;
  return rep('p'+x.key,
   `<span class="tm">${x.time}</span><b>${esc(x.title)}</b><span class="note">${x.n} ${x.n===1?'Runde':'Runden'}${x.mails>1?', '+x.mails+' Berichte':''}</span><span class="sm">${so?'Kill Points '+f(t.kp_gained)+' · Kill Count '+f(t.enemy_severe)+' · Trade ':''}${so?`<b class="${trc(t.enemy_severe,t.own_severe)}">${tr(t.enemy_severe,t.own_severe)}</b>`:''}</span>`,
   facts([['Runden',x.n],['Gegner',x.foes],['Kill Points',f(t.kp_gained)],['Gegner-Kill-Points',f(t.kp_lost)],['Kill Count (Gegner schwer verwundet)',f(t.enemy_severe)],['Gegner leicht verwundet',f(t.enemy_hurt)],['Eigene schwer verwundet',f(t.own_severe),'bad'],['Eigene leicht verwundet',f(t.own_hurt)],['Eigene Tote',f(t.own_dead)],['Gegner-Tote',f(t.enemy_dead)],['Trade schwer verwundet (100 % = ausgeglichen)',tr(t.enemy_severe,t.own_severe),trc(t.enemy_severe,t.own_severe)],['Trade alle Verwundeten',tr(t.enemy_severe+t.enemy_hurt,t.own_severe+t.own_hurt),trc(t.enemy_severe+t.enemy_hurt,t.own_severe+t.own_hurt)],['Trade Kill Points',tr(t.kp_gained,t.kp_lost),trc(t.kp_gained,t.kp_lost)]])+
   '<h3>Die einzelnen Gegner</h3>'+tbl(['Gegner','Runden','Kill Points','Gegner-KP','Kill Count (Gegner schwer)','Gegner leicht','Eigene schwer','Eigene leicht','Trade'],
        x.rounds.map(r=>`<tr><td>${esc(r.foe)}</td><td>${r.n}</td><td>${f(r.kp_gained)}</td><td>${f(r.kp_lost)}</td><td>${f(r.enemy_severe)}</td><td>${f(r.enemy_hurt)}</td><td class="bad">${f(r.own_severe)}</td><td>${f(r.own_hurt)}</td><td class="${trc(r.enemy_severe,r.own_severe)}">${tr(r.enemy_severe,r.own_severe)}</td></tr>`)))}).join('');
 $('pvsec').hidden=!s.pvp.length;$('c_pv').textContent=s.pvp.length||'';
 $('pv').innerHTML=pvList(s.pvp);
 $('aosec').hidden=!s.aoo.length;$('c_ao').textContent=s.aoo.length||'';
 $('aokpi').innerHTML=po.n?kpi('Kämpfe',f(po.n))+kpi('Kill Points',f(po.kp_gained))+kpi('Kill Count (Gegner schwer verwundet)',f(po.enemy_severe))+kpi('Eigene schwer verwundet',f(po.own_severe),'bad')+kpi('Trade (schwer verwundet)',tr(po.enemy_severe,po.own_severe),trc(po.enemy_severe,po.own_severe)):'';
 $('ao').innerHTML=pvList(s.aoo);
 const PF=Object.fromEntries(s.profile_fields),dd=v=>(v>0?'+':'')+f(v);
 const spark=(rows,k)=>{const r=rows.filter(x=>x[k]!=null).reverse();if(r.length<2)return'';const v=r.map(x=>x[k]),lo=Math.min(...v),hi=Math.max(...v),W=260,H=48,
   pts=r.map((x,i)=>(i/(r.length-1)*(W-8)+4).toFixed(1)+','+(H-6-(hi===lo?.5:(x[k]-lo)/(hi-lo))*(H-12)).toFixed(1)).join(' ');
   return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Verlauf ${PF[k]||k}"><polygon points="4,${H} ${pts} ${W-4},${H}" fill="var(--accsoft)"/><polyline points="${pts}" fill="none" stroke="var(--acc)" stroke-width="2" stroke-linejoin="round"/></svg>`};
 $('pf').innerHTML=s.profile.map(p=>{const ks=s.profile_fields.map(x=>x[0]).filter(k=>p.last[k]!=null).concat(Object.keys(p.last).filter(k=>!PF[k]));
   return `<h3>${esc(p.name||p.account)} <span class="note">ID ${p.account} · Stand ${dm(p.last_day)}${p.base_day!==p.last_day?' · Veränderung seit '+dm(p.base_day):''}</span></h3>
   <div class="grid">${ks.map(k=>`<div class="k"><b>${f(p.last[k])}</b><span>${esc(PF[k]||k)}</span>${p.delta[k]!=null?`<span class="${p.delta[k]>=0?'good':'bad'}">${dd(p.delta[k])}</span>`:''}${spark(p.rows,k)}</div>`).join('')}</div>
   <div class="wrap"><table><tr><th>Tag</th>${ks.map(k=>`<th>${esc(PF[k]||k)}</th>`).join('')}</tr>${p.rows.map(r=>`<tr><td>${dm(r.day)}</td>${ks.map(k=>`<td>${r[k]==null?'–':f(r[k])}</td>`).join('')}</tr>`).join('')}</table></div>`}).join('')||none('Noch keine Profil-Daten im gewählten Zeitraum. Schick mir einen Profil-Screenshot, ich erstelle dir die Datei zum Einfügen.');
 const compact=n=>n>=1e9?(n/1e9).toFixed(2)+(LANG==='en'?' B':' Mrd'):n>=1e6?(n/1e6).toFixed(2)+(LANG==='en'?' M':' Mio'):n>=1e4?(n/1e3).toFixed(1)+(LANG==='en'?' K':' Tsd'):f(n);
 const accSel=$('acc').value,pa=accSel?s.accounts.find(x=>x.account===accSel):(s.accounts.length===1?s.accounts[0]:null);
 const nm=pa?(pa.acc_name||pa.account):(s.accounts.length>1?'Alle Accounts':'Barbaren-Tracker');
 $('hname').textContent=nm+(pa&&pa.account===s.main_account?' ★':'');$('hav').textContent=nm.normalize('NFKD').replace(/[^A-Za-z0-9]/g,'').slice(0,2).toUpperCase()||'⚔';
 const pr=s.profile.find(x=>pa&&x.account===pa.account)||(s.profile.length===1&&!(s.accounts.length>1&&!pa)?s.profile[0]:null);
 $('hchips').innerHTML=pr?[['power','⚡','Macht'],['kill_points','⚔️','Kill Points'],['dead','💀','Tote'],['helps','🤝','Hilfen'],['gathered','🧺','Gesammelt']].filter(x=>pr.last[x[0]]!=null)
   .map(x=>`<div class="chip"><b>${x[1]} ${compact(pr.last[x[0]])}</b><span>${x[2]}${pr.delta[x[0]]?`<em class="${pr.delta[x[0]]>=0?'good':'bad'}">${dd(pr.delta[x[0]])}</em>`:''}</span></div>`).join(''):'';
 showTab(tab);
 $('dt').innerHTML='<tr><th>Tag</th><th>Solo-Berichte</th><th>Rallys</th><th>Festungen</th><th>Gegner (solo)</th><th>Barbaren-Truppen (solo)</th><th>Größter Gegner</th><th>XP</th><th>Verwundet (solo)</th><th>Spielerkampf-Berichte</th></tr>'+
  s.days.filter(d=>d.day>=a&&d.day<=b).map(d=>`<tr><td>${dm(d.day)}</td><td>${f(d.solo)}</td><td>${f(d.rallies)}</td><td>${f(d.forts)}</td><td>${f(d.foes)}</td><td>${f(d.killed)}</td><td>${f(d.maxfoe)}</td><td>${f(d.xp)}</td><td>${f(d.hurt)}</td><td>${f(d.pvp)}</td></tr>`).join('')}
$('mainbtn').onclick=async()=>{const a=$('acc').value;if(!a)return;const cur=lastMain;await fetch('/api/main',{method:'POST',body:JSON.stringify({account:cur===a?'':a})});lastSig='';load()};
$('pbtn').onclick=()=>$('pfile').click();
$('pfile').onchange=async e=>{let n=0,err='';for(const fl of e.target.files){try{const r=await fetch('/api/profile',{method:'POST',body:await fl.text()}).then(r=>r.json());if(r.ok)n+=r.n;else err=r.error}catch(x){err=String(x)}}
 $('pfmsg').textContent=err?'Fehler: '+err:n+' Eintrag/Einträge übernommen.';e.target.value='';lastSig='';load()};
['range','d1','d2','acc'].forEach(i=>$(i).onchange=()=>{lastSig='';load()});load();setInterval(load,4000);
// ===== Sprache: Deutsch / English (RoK-Begriffe) =====
const EN=[
[`Barbaren-Tracker`,`Barbarian Tracker`],[`Heute`,`Today`],[`Gestern`,`Yesterday`],[`Letzte 7 Tage`,`Last 7 days`],[`Letzte 30 Tage`,`Last 30 days`],
[`Dieser Monat`,`This month`],[`Alles`,`All time`],[`Zeitraum wählen`,`Custom range`],[`von`,`from`],[`bis`,`to`],[`und`,`and`],
[`Alle Berichte aufklappen`,`Expand all reports`],[`zuklappen`,`Collapse all`],
[`Beute`,`Loot`],[`Festungen`,`Forts`],[`Barbarenkämpfe`,`Barbarian Battles`],[`Spielerkämpfe`,`PvP Battles`],[`Profil`,`Profile`],[`Pro Tag`,`Per Day`],[`Namen eintragen`,`Enter Names`],
[`offen`,`open`],[`weitere`,`more`],[`letzte`,`last`],
[`Solo-Berichte`,`Solo reports`],[`Rallys`,`Rallies`],[`Festungen zerstört`,`Forts destroyed`],[`Gegner besiegt (solo)`,`Enemies defeated (solo)`],
[`Barbaren-Truppen besiegt (solo)`,`Barbarian troops defeated (solo)`],[`Barbaren-Truppen (solo)`,`Barbarian troops (solo)`],[`Barbaren-Truppen`,`Barbarian troops`],
[`Kommandanten-XP`,`Commander XP`],[`Leicht verwundet (solo)`,`Lightly wounded (solo)`],[`Schwer verwundet (solo)`,`Severely wounded (solo)`],
[`Kill Count (Gegner schwer verwundet)`,`Kill Count (enemy severely wounded)`],[`Kill Count (Gegner schwer)`,`Kill Count (enemy severe)`],
[`Eigene schwer verwundet`,`Own severely wounded`],[`Eigene leicht verwundet`,`Own lightly wounded`],[`Eigene schwer`,`Own severe`],[`Eigene leicht`,`Own light`],
[`Trade (schwer verwundet)`,`Trade (severely wounded)`],[`Trade schwer verwundet (100 % = ausgeglichen)`,`Trade severely wounded (100 % = even)`],
[`Trade alle Verwundeten`,`Trade all wounded`],[`Trade Kill Points`,`Trade kill points`],
[`Gegner-Kill-Points`,`Enemy kill points`],[`Gegner-Truppen`,`Enemy troops`],[`Gegner-Tote`,`Enemy dead`],[`Gegner-KP`,`Enemy KP`],[`Gegner leicht verwundet`,`Enemy lightly wounded`],[`Gegner leicht`,`Enemy light`],
[`Eigene Truppen`,`Own troops`],[`Eigene Tote`,`Own dead`],[`Gegner im Bericht`,`Enemies in report`],[`Die einzelnen Gegner`,`The individual enemies`],[`Größter Gegner`,`Largest enemy`],
[`Gegner (solo)`,`Enemies (solo)`],[`Verwundet (solo)`,`Wounded (solo)`],[`Spielerkampf-Berichte`,`PvP battle reports`],
[`Leicht verwundet`,`Lightly wounded`],[`Schwer verwundet`,`Severely wounded`],[`verwundet`,`wounded`],[`Verwundeten`,`wounded`],[`schwer`,`severely`],[`leicht`,`lightly`],
[`Gegner`,`Enemy`],[`Eigene`,`Own`],[`Kämpfe`,`Battles`],[`Spielerkampf`,`PvP battle`],[`Tote`,`Dead`],[`Geheilt`,`Healed`],
[`Rally gestartet`,`Rally started`],[`Rally beigetreten`,`Rally joined`],[`Barbarenfestung`,`Barbarian Fort`],[`Barbaren`,`Barbarians`],
[`Anteil`,`Share`],[`Dein Schadensanteil`,`Your damage share`],[`Teilnehmer`,`Participants`],[`Kommandant`,`Commander`],[`Runden`,`rounds`],[`Runde`,`round`],
[`Berichte`,`reports`],[`Bericht`,`report`],[`Zum Bericht`,`Go to report`],[`Speichern`,`Save`],[`Ignorieren`,`Ignore`],[`Alle ignorieren`,`Ignore all`],
[`Ignoriert`,`Ignored`],[`Wieder anzeigen`,`Show again`],[`gespeichert`,`saved`],[`Fehler`,`Error`],[`neu`,`new`],[`gefunden`,`found`],
[`Sortieren nach`,`Sort by`],[`Häufigkeit`,`Frequency`],[`Datum: zuletzt gesehen (neueste zuerst)`,`Date: last seen (newest first)`],[`Datum: zuerst gesehen (älteste zuerst)`,`Date: first seen (oldest first)`],
[`Wert/Stück`,`Value/unit`],[`Stück`,`pcs`],[`Min`,`min`],
[`Training-Speedups`,`Training Speedups`],[`Universal-Speedups`,`Universal Speedups`],[`Heilungs-Speedups`,`Healing Speedups`],
[`Training-Speedup (Min)`,`Training Speedup (min)`],[`Universal-Speedup (Min)`,`Universal Speedup (min)`],[`Heilungs-Speedup (Min)`,`Healing Speedup (min)`],
[`Training-Speedup`,`Training Speedup`],[`Universal-Speedup`,`Universal Speedup`],[`Heilungs-Speedup`,`Healing Speedup`],
[`Holz`,`Wood`],[`Stein`,`Stone`],[`Mais`,`Food`],[`Edelsteine`,`Gems`],[`XP-Buch`,`Tome of Knowledge`],[`XP aus Büchern`,`XP from Tomes`],
[`Ressourcenpakete`,`Resource Packs`],[`Ressourcenpaket`,`Resource Pack`],[`Aktionspunkte (Notfall)`,`Action Points (Emergency)`],[`Aktionspunkte`,`Action Points`],
[`Notfall-AP-Wiederherstellung`,`Emergency AP Recovery`],[`vermutet`,`guessed`],[`Sonstiges`,`Other`],
[`Macht`,`Power`],[`Höchste Macht`,`Highest Power`],[`Gebäude-Macht`,`Building Power`],[`Technologie-Macht`,`Tech Power`],[`Truppen-Macht`,`Troop Power`],[`Kommandanten-Macht`,`Commander Power`],
[`Siege`,`Victories`],[`Niederlagen`,`Defeats`],[`Späher-Einsätze`,`Scout Missions`],[`Hilfen`,`Helps`],[`Gesammelt`,`Gathered`],[`Ressourcenhilfe`,`Resource Assistance`],
[`T1-Kills`,`T1 Kills`],[`T2-Kills`,`T2 Kills`],[`T3-Kills`,`T3 Kills`],[`T4-Kills`,`T4 Kills`],[`T5-Kills`,`T5 Kills`],
[`Tag`,`Day`],[`Stand`,`As of`],[`Veränderung seit`,`Change since`],[`Verlauf`,`Trend`],[`Gesamter Zeitraum`,`Entire period`],
[`Alle Accounts`,`All accounts`],[`★ Haupt-Account`,`★ Main account`],[`☆ Als Haupt-Account`,`☆ Set as main account`],[`Haupt-Account wieder entfernen`,`Remove main account`],
[`Beim Start immer diesen Account anzeigen`,`Always show this account on start`],[`Wähle zuerst einen einzelnen Account aus`,`Select a single account first`],
[`Kommandant #`,`Commander #`],[`keine Beute`,`no loot`],[`Keine Beute in diesem Bericht.`,`No loot in this report.`],[`Das hast du bekommen`,`What you received`],
[`noch nicht benannt`,`not named yet`],[`Noch nicht benannt. Klicken zum Benennen.`,`Not named yet. Click to name it.`],[`Noch nicht benannt:`,`Not named yet:`],
[`Keine Beute im gewählten Zeitraum.`,`No loot in the selected period.`],[`Keine Festungen im gewählten Zeitraum.`,`No forts in the selected period.`],[`Keine Kämpfe im gewählten Zeitraum.`,`No battles in the selected period.`],
[`Ordner nicht gefunden. Pfad mit --folder angeben.`,`Folder not found. Specify the path with --folder.`],[`Keine Berichts-Dateien gefunden in:`,`No report files found in:`],[`Inhalt:`,`Contents:`],[`(leer)`,`(empty)`],
[`Profil-Datei einfügen …`,`Import profile file …`],
[`Zu dieser Festung gibt es keine Belohnungs-Mail im Ordner, deshalb fehlen Level, Position und Beute.`,`There is no reward mail for this fort in the folder, so level, position and loot are missing.`],
[`Truppen und Verluste gelten bei Rallys für alle Teilnehmer zusammen und werden nicht einzeln angezeigt.`,`In rallies, troops and losses apply to all participants combined and are not shown individually.`],
[`Barbaren-Truppen = Summe der Truppenstärke aller besiegten Gegner (nicht die Anzahl). Zahlen zu Truppen und Verlusten aus Rallys gelten für alle Teilnehmer zusammen und sind hier nicht eingerechnet.`,`Barbarian troops = sum of the troop strength of all defeated enemies (not the number). Troop and loss figures from rallies apply to all participants combined and are not included here.`],
[`Zusammengehörende Berichte stehen in einer Zeile. Ark of Osiris steht getrennt im eigenen Reiter und ist hier nicht mitgezählt.`,`Related reports are shown in one row. Ark of Osiris has its own tab and is not counted here.`],
[`Ark of Osiris, getrennt von den anderen Spielerkämpfen gezählt.`,`Ark of Osiris, counted separately from the other PvP battles.`],
[`Profilwerte aus deinen Screenshots. Die Datei, die ich dir schicke, fügst du hier ein. Der Verlauf wird gespeichert und nichts geht beim erneuten Einfügen doppelt verloren.`,`Profile values from your screenshots. Import the file I send you here. The history is saved, and importing again never creates duplicates.`],
[`Noch keine Profil-Daten im gewählten Zeitraum. Schick mir einen Profil-Screenshot, ich erstelle dir die Datei zum Einfügen.`,`No profile data in the selected period yet. Send me a profile screenshot and I will create the file to import.`],
[`Diese Nummern kennt das Programm noch nicht. Schau im Spiel nach (Tooltip auf das Item, Kommandant im Bericht) und trage den Namen ein. Gilt sofort auch für alle alten Berichte.`,`The program does not know these IDs yet. Look them up in the game (item tooltip, commander in the report) and enter the name. It applies to all older reports immediately.`],
[`Alle offenen Namen ignorieren. Sobald einer in einem neuen Bericht auftaucht, erscheint er wieder.`,`Ignore all open names. Each one reappears as soon as it shows up in a new report.`],
[`Unbenannte Items erscheinen in den Berichten gelb markiert. Ein Klick darauf führt hierher, und "Zum Bericht" führt zurück. Ignorierte Namen erscheinen wieder, sobald sie in einem neuen Bericht vorkommen (Markierung "neu").`,`Unnamed items are highlighted in yellow in the reports. Clicking one leads here, and "Go to report" leads back. Ignored names reappear as soon as they show up in a new report (marked "new").`],
[`Wert pro Stück, z. B. 5 für 5 Min oder 1000 für 1.000 Holz`,`Value per unit, e.g. 5 for 5 min or 1000 for 1,000 wood`],
[`Neuester Bericht mit diesem Item`,`Latest report with this item`],
[`Vorerst ignorieren: nicht in der Liste offener Namen. Taucht er in einem neuen Bericht auf, erscheint er wieder.`,`Ignore for now: hidden from the open names list. It reappears if it shows up in a new report.`],
[`Beschleunigt die Ausbildung und Aufwertung von Truppen.`,`Speeds up training and upgrading of troops.`],
[`Universal-Beschleunigung: gilt für Bau, Forschung, Ausbildung und Heilung.`,`Universal Speedup: works for building, research, training and healing.`],
[`Beschleunigt die Heilung verwundeter Truppen.`,`Speeds up healing of wounded troops.`],
[`Mais: Rohstoff für Truppen und Gebäude.`,`Food: resource for troops and buildings.`],[`Holz: Rohstoff für Gebäude, Forschung und Truppen.`,`Wood: resource for buildings, research and troops.`],
[`Stein: Rohstoff für Gebäude, Forschung und Truppen.`,`Stone: resource for buildings, research and troops.`],[`Gold: Rohstoff für Gebäude, Forschung und Truppen.`,`Gold: resource for buildings, research and troops.`],
[`Edelsteine: Premium-Währung.`,`Gems: premium currency.`],[`Gibt einem Kommandanten Erfahrung (XP).`,`Gives a commander experience (XP).`],
[`Ressourcenpaket. Lvl 2: zufällig 10.000 Mais, 10.000 Holz, 7.500 Stein oder 5.000 Gold (aus Barbarenfestungen, Hungry-Turkey-Event). Lvl 1 (C): zufällig 1.000 Mais, 1.000 Holz, 750 Stein oder 500 Gold.`,`Resource Pack. Lvl 2: random 10,000 Food, 10,000 Wood, 7,500 Stone or 5,000 Gold (from Barbarian Forts, Hungry Turkey event). Lvl 1 (C): random 1,000 Food, 1,000 Wood, 750 Stone or 500 Gold.`],
[`Notfall-Aktionspunkte: stellen sofort Aktionspunkte für Angriffe auf Barbaren wieder her.`,`Emergency Action Points: instantly restore action points for attacking barbarians.`]
];
const ENM=new Map(EN.map(([a,b])=>[a,b]));
const ENRE=new RegExp('(?<![\\p{L}\\p{N}])(?:'+EN.map(x=>x[0]).sort((a,b)=>b.length-a.length).map(s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')+')(?![\\p{L}\\p{N}])','giu');
const DYN=[[/Überwache (\d+) Dateien \((\d+) neu seit Start\)/g,'Watching $1 files ($2 new since start)'],[/Lese gerade noch (\d+) Dateien, neueste zuerst …/g,'Still reading $1 files, newest first …'],
 [/(\d+) Eintrag\/Einträge übernommen\./g,'$1 entries imported.'],
 [/(?<!\d)(\d{1,2})\.(\d{2})\. (\d{2}:\d{2})/g,(m,d,mo,t)=>+d+' '+MON[+mo-1]+' '+t]];
function enCase(m){let e=ENM.get(m);if(e!=null)return e;const a=m[0],up=a===a.toUpperCase()&&a!==a.toLowerCase(),alt=(up?a.toLowerCase():a.toUpperCase())+m.slice(1);e=ENM.get(alt);
 if(e==null)return m;return up?e[0].toUpperCase()+e.slice(1):e[0].toLowerCase()+e.slice(1)}
function enText(s){if(!/[A-Za-zÄÖÜäöüß]|\d\.\d{2}\./.test(s))return s;for(const [r,v] of DYN)s=s.replace(r,v);return s.replace(ENRE,enCase)}
const ENATTR=['title','placeholder','data-tip','aria-label'];
function enNode(n){
 if(n.nodeType===3){const v=enText(n.nodeValue);if(v!==n.nodeValue)n.nodeValue=v;return}
 if(n.nodeType!==1||n.tagName==='SCRIPT'||n.tagName==='STYLE')return;
 if(n.tagName==='INPUT'&&n.classList.contains('n')&&!n.dataset.en){n.dataset.en=1;n.value=enText(n.value)}
 for(const a of ENATTR){const v=n.getAttribute(a);if(v){const w=enText(v);if(w!==v)n.setAttribute(a,w)}}
 for(const c of n.childNodes)enNode(c)}
if(LANG==='en'){
 document.documentElement.lang='en';document.title='Barbarian Tracker';
 const mo=new MutationObserver(ms=>{mo.disconnect();for(const m of ms){if(m.type==='childList')m.addedNodes.forEach(enNode);else if(m.type==='characterData')enNode(m.target);else enNode(m.target)}
  mo.observe(document.body,MO)});
 const MO={subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:ENATTR};
 enNode(document.body);mo.observe(document.body,MO)}
$('lang').textContent=LANG==='en'?'Deutsch':'English';
$('lang').onclick=()=>{try{localStorage.setItem('roklang',LANG==='en'?'de':'en')}catch(e){}location.reload()};
</script></body></html>"""

def make_handler(db, watcher):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def send(self, body, ctype):
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self.send(PAGE, "text/html")
            elif u.path == "/api/stats":
                q = parse_qs(u.query)
                self.send(json.dumps(db.stats(q.get("from", ["0000-01-01"])[0], q.get("to", ["9999-12-31"])[0],
                                              q.get("account", [""])[0])), "application/json")
            elif u.path.startswith("/icon/"):
                key = u.path[6:]
                folder = os.path.join(watcher.data_dir, "icons")
                for ext, ct in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".webp", "image/webp"), (".gif", "image/gif")):
                    fp = os.path.join(folder, key + ext)
                    if re.fullmatch(r"[a-z0-9_]+", key) and os.path.isfile(fp):
                        with open(fp, "rb") as fh:
                            data = fh.read()
                        self.send_response(200)
                        self.send_header("Content-Type", ct)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "max-age=60")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                self.send_error(404)
            elif u.path == "/api/status":
                self.send(json.dumps(watcher.status), "application/json")
            else:
                self.send_error(404)
        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/profile":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 2000000)))
                    snaps = d.get("snapshots", []) if isinstance(d, dict) else d
                    self.send(json.dumps({"ok": True, "n": db.add_profile(snaps)}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path == "/api/main":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 1000)))
                    acc = str(d.get("account", ""))
                    if acc and not acc.isdigit():
                        raise ValueError("ungültige Account-ID")
                    db.set_setting("main_account", acc)
                    self.send(json.dumps({"ok": True}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path == "/api/ignore":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 1000)))
                    kind = d.get("kind", "item")
                    if kind == "item":
                        db.set_ignored(int(d["id"]), bool(d.get("ignore", True)))
                    else:
                        db.set_ignored_other(kind, int(d["id"]), bool(d.get("ignore", True)))
                    self.send(json.dumps({"ok": True}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path != "/api/name":
                return self.send_error(404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                d = json.loads(self.rfile.read(min(n, 10000)))
                save_name(d["kind"], d["id"], d["name"], d.get("unit"), d.get("ikind"))
                self.send(json.dumps({"ok": True}), "application/json")
            except Exception as e:
                self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
    return H

def main():
    for _n in ("stdout", "stderr"):        # ohne Konsole (pythonw / .pyw) gibt es keine Ausgabe
        if getattr(sys, _n) is None:
            setattr(sys, _n, open(os.devnull, "w"))
    ap = argparse.ArgumentParser(description="RoK Barbaren-Tracker")
    ap.add_argument("--folder", default=DEFAULT_FOLDER, help="Pfad zum mailcache-Ordner")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=DATA_DIR, help="Ordner für Datenbank und Beispiel-Berichte")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.data, exist_ok=True)
    db_path = os.path.join(args.data, "rok_barbs.db")
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rok_barbs.db")
    if not os.path.exists(db_path) and os.path.exists(legacy):
        shutil.copy2(legacy, db_path)       # Daten der früheren Version übernehmen
    global NAMES_PATH
    NAMES_PATH = os.path.join(args.data, "namen.json")
    load_names(NAMES_PATH)
    db = DB(db_path)
    w = Watcher(args.folder, db, args.data)
    w.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(db, w))
    url = "http://127.0.0.1:%d" % args.port
    print("Barbaren-Tracker läuft: %s\nÜberwachter Ordner: %s\nStatistik gespeichert in: %s\nBeenden mit Strg+C"
          % (url, args.folder, db_path))
    if not os.path.isdir(args.folder):
        print("WARNUNG: Ordner nicht gefunden. Mit --folder den richtigen Pfad angeben.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")

if __name__ == "__main__":
    main()
