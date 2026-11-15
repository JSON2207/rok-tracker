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
import argparse, base64, json, os, re, shutil, sqlite3, struct, sys, threading, time, webbrowser
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
# Allianz-Rangliste: Kategorien (Schluessel, Spieltitel). Resource Assistance und Armory Contributions werden bewusst nicht erfasst.
ALLIANCE_CATS = [("power", "Individual Power"), ("kills", "Killing Machine"), ("tech", "Mad Scientist"),
                 ("build", "Master Builder"), ("help", "Alliance Help"), ("forts", "Fort Destroyer"), ("mob", "Alliance Mobilization"), ("aoo", "Ark of Osiris")]
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
        CREATE TABLE IF NOT EXISTS alliance(day TEXT, cat TEXT, name TEXT, rank INT, value INT, PRIMARY KEY(day, cat, name));
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

    def avatars(self):
        out = {}
        for r in self.q("SELECT k, v FROM settings WHERE k LIKE 'avatar:%'"):
            out[r["k"][7:]] = r["v"].split("|", 1)[0]
        return out

    def get_avatar(self, key):
        r = self.get_setting("avatar:" + key, "")
        parts = r.split("|", 2)
        return (parts[1], parts[2]) if len(parts) == 3 else None

    def set_avatar(self, key, dataurl):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", key):
            raise ValueError("ungültiger Schlüssel")
        if not dataurl:
            with self.lock:
                self.c.execute("DELETE FROM settings WHERE k=?", ("avatar:" + key,))
                self.c.commit()
            return
        m = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)", dataurl)
        if not m or len(dataurl) > 900000:
            raise ValueError("Bild ungültig oder zu groß")
        self.set_setting("avatar:" + key, "%d|%s|%s" % (int(time.time()), m.group(1), m.group(2)))

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

    def add_alliance(self, snaps):
        """Allianz-Ranglisten (aus Screenshots abgelesen). Format: {"date": "...", "categories": {"power": [{"rank", "name", "value"}]}}.
        Mehrere Dateien pro Tag werden zusammengefuehrt; derselbe Name am selben Tag wird ueberschrieben.
        Mit "replace": true ersetzt die Datei die Liste dieses Tages je Kategorie komplett."""
        if isinstance(snaps, dict):
            snaps = snaps.get("snapshots", [snaps])
        valid = {k for k, _ in ALLIANCE_CATS}
        n = 0
        with self.lock:
            for s in snaps:
                day = str(s["date"])[:10]
                datetime.strptime(day, "%Y-%m-%d")
                for cat, rows in (s.get("categories") or {}).items():
                    if cat not in valid:
                        raise ValueError("Unbekannte Kategorie: %s" % cat)
                    if s.get("replace"):                        # vollstaendige Liste: Eintraege dieses Tages ersetzen
                        self.c.execute("DELETE FROM alliance WHERE day=? AND cat=?", (day, cat))
                    for r in rows:
                        name = str(r["name"]).strip()
                        if not name:
                            continue
                        rank = int(r["rank"]) if r.get("rank") is not None else None
                        val = int(r["value"]) if r.get("value") is not None else None
                        self.c.execute("INSERT OR REPLACE INTO alliance VALUES(?,?,?,?,?)", (day, cat, name, rank, val))
                        n += 1
            self.c.commit()
        return n

    def alliance_ui(self):
        """Gespeicherte Ansicht der Allianz-Seite: Reihenfolge, ausgeschaltete Kategorien, Sortierung."""
        try:
            d = json.loads(self.get_setting("alliance_ui", "{}"))
        except ValueError:
            d = {}
        keys = [k for k, _ in ALLIANCE_CATS]
        order = [k for k in d.get("order", []) if k in keys]
        order += [k for k in keys if k not in order]
        off = [k for k in d.get("off", []) if k in keys]
        srt = d.get("sort") if d.get("sort") in keys + ["avg_all"] else "avg_all"
        return dict(order=order, off=off, sort=srt, dir=-1 if d.get("dir") == -1 else 1)

    def set_alliance_ui(self, d):
        cur = self.alliance_ui()
        keys = [k for k, _ in ALLIANCE_CATS]
        if isinstance(d.get("order"), list):
            cur["order"] = [k for k in d["order"] if k in keys]
        if isinstance(d.get("off"), list):
            cur["off"] = [k for k in d["off"] if k in keys]
        if d.get("sort") in keys + ["avg_all"]:
            cur["sort"] = d["sort"]
        if d.get("dir") in (1, -1):
            cur["dir"] = d["dir"]
        self.set_setting("alliance_ui", json.dumps(cur))
        return self.alliance_ui()

    def alliance_stats(self):
        rows = self.q("SELECT day, cat, name, rank, value FROM alliance ORDER BY day")
        days, members = {}, {}
        for r in rows:
            days.setdefault(r["cat"], set()).add(r["day"])
            m = members.setdefault(r["name"], {"name": r["name"], "c": {}, "h": {}})
            m["h"].setdefault(r["cat"], []).append([r["day"], r["value"], r["rank"]])
        latest = {c: max(d) for c, d in days.items()}
        for m in members.values():
            for cat, hist in list(m["h"].items()):
                last = hist[-1]
                if last[0] != latest[cat]:                      # in der neuesten Liste nicht mehr aufgefuehrt
                    m["c"][cat] = dict(v=None, r=None, d=None, day=latest[cat], seen=last[0])
                    continue
                prev = next((h for h in reversed(hist[:-1]) if h[1] is not None), None)
                m["c"][cat] = dict(v=last[1], r=last[2], day=last[0], pd=prev[0] if prev else None,
                                   d=(last[1] - prev[1]) if prev and last[1] is not None else None)
        ui = self.alliance_ui()
        names = dict(ALLIANCE_CATS)
        return dict(cats=[dict(key=k, label=names[k]) for k in ui["order"]], ui=ui, latest=latest,
                    days={c: sorted(d) for c, d in days.items()}, members=sorted(members.values(), key=lambda m: m["name"].lower()))

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
        return dict(unknown=self.unknown(), totals=tot, pvp_totals=pv, aoo_totals=po, aoo=aoos, profile=self.profile_stats(d_from, d_to, account), main_account=self.get_setting("main_account"), avatars=self.avatars(),
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
body{margin:0;color:var(--ink);font:15px/1.5 Inter,"Segoe UI",system-ui,sans-serif;min-height:100vh;background:radial-gradient(900px 420px at 12% -8%,rgba(59,130,246,.18),transparent 62%) no-repeat,radial-gradient(720px 360px at 98% 0,rgba(52,211,153,.09),transparent 60%) no-repeat,var(--bg);background-attachment:fixed}
html{background:var(--bg)}
main{max-width:var(--mw,1600px);margin-left:var(--ml,auto);margin-right:var(--mr,auto);padding:22px 16px 70px}
.hero{display:grid;grid-template-columns:auto 1fr;gap:20px;padding:22px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(135deg,var(--soft),var(--card));margin-bottom:14px;box-shadow:0 18px 40px -26px rgba(0,0,0,.7)}
.av{width:88px;height:88px;border-radius:50%;padding:3px;background:conic-gradient(from 210deg,#3b82f6,#34d399,#fbbf24,#3b82f6);position:relative;cursor:pointer}
.av img{width:100%;height:100%;border-radius:50%;object-fit:cover;display:block}
.av::after{content:"📷";position:absolute;inset:3px;border-radius:50%;display:grid;place-items:center;background:rgba(0,0,0,.55);font-size:1.5rem;opacity:0;transition:opacity .15s}
.av:hover::after,.av:focus-visible::after{opacity:1}
.avc{display:flex;flex-direction:column;align-items:center;gap:6px}
.avc button{background:none;border:0;padding:0;color:var(--mute);font-size:.76rem;text-decoration:underline}
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
.tabs button{border:0;border-radius:11px;background:none;color:var(--mute);padding:9px 13px;white-space:nowrap;font-weight:600}
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
#al td{vertical-align:top}#al th[data-k]{cursor:pointer;user-select:none}#al th.on{color:var(--ink)}
#al tr.m{cursor:pointer}#al td.n{white-space:nowrap;font-weight:600}
.rk{color:var(--mute);font-size:.74rem;margin-left:6px}.dl{display:block;font-size:.74rem}
#al tr.det td{white-space:normal;text-align:left;background:var(--soft)}
.hist{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px;padding:6px 4px}.hist>div{min-width:0;overflow-x:auto}.hist h3{margin:0 0 4px;font-size:.88rem}
.hist table{font-size:.82rem}.hist td,.hist th{padding:3px 8px}
.topn b{font-size:1.1rem!important;overflow-wrap:anywhere}
.mbtn{position:fixed;top:12px;left:12px;z-index:60;padding:5px 11px;font-size:1.2rem;line-height:1.2;cursor:pointer}body.sbopen .mbtn#mbtn{display:none}.sb{position:fixed;top:0;left:0;bottom:0;width:260px;z-index:70;background:var(--card);border-right:1px solid var(--line);display:flex;flex-direction:column;transform:translateX(-102%);transition:transform .18s ease;overflow-y:auto}body.sbopen .sb{transform:none}.sbh{display:flex;align-items:center;justify-content:space-between;padding:16px 14px 12px 18px}.sbt{font-weight:800;font-size:1.15rem;letter-spacing:.02em}.sbh .mbtn{position:static;padding:3px 9px;font-size:1rem}.sbn{display:flex;flex-direction:column;padding:6px 10px 20px;gap:2px}.sbl{font-size:.72rem;color:var(--mute);padding:10px 8px 4px}.sbn a{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:9px;text-decoration:none;color:var(--ink)}.sbn a svg{flex:none;color:var(--mute)}.sbn a:hover{background:var(--soft)}.sbn a.cur{background:var(--accsoft);color:var(--acc);font-weight:700}.sbn a.cur svg{color:var(--acc)}.msep{height:1px;background:var(--line);margin:10px 2px 4px}.mrow{display:flex;flex-direction:column;gap:5px;padding:6px 8px;font-size:.82rem;color:var(--mute)}.mrow select{width:100%}.seg{display:flex;gap:4px}.seg button{flex:1;padding:5px 2px;font-size:.78rem;cursor:pointer}.seg button.on{background:var(--acc);border-color:var(--acc);color:#fff}.sbov{display:none}body{transition:padding-left .18s ease}@media(min-width:1100px){body.sbopen{padding-left:260px}}@media(max-width:1099px){body.sbopen .sbov{display:block;position:fixed;inset:0;background:#0007;z-index:65}}

</style></head><body><main>
<button type="button" id="mbtn" class="mbtn" aria-label="Menü" aria-expanded="true" title="Menü">☰</button><aside id="sb" class="sb" aria-label="Menü"><div class="sbh"><span class="sbt">RoK&nbsp;Tracker</span><button type="button" id="sbx" class="mbtn" aria-label="Menü schließen" title="Menü schließen">⇤</button></div><nav class="sbn"><div class="sbl">Seiten</div><a href="/" data-p="/"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4v5c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V7z"/><path d="M9 12l2 2 4-4"/></svg><span>Loot-Tracker</span></a><a href="/allianz" data-p="/allianz"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg><span>Allianz-Ranking</span></a><div class="msep"></div><div class="sbl">Ansicht</div><label class="mrow"><span>Sprache</span><select id="lang" aria-label="Language / Sprache"><option value="de">🌐 Deutsch</option><option value="en">🌐 English</option></select></label><div class="mrow"><span>Breite</span><span class="seg" id="mw"><button type="button" data-v="900">Schmal</button><button type="button" data-v="1160">Normal</button><button type="button" data-v="1600">Breit</button><button type="button" data-v="100%">Voll</button></span></div><div class="mrow"><span>Ausrichtung</span><span class="seg" id="ma"><button type="button" data-v="l">Links</button><button type="button" data-v="c">Mitte</button><button type="button" data-v="r">Rechts</button></span></div></nav></aside><div id="sbov" class="sbov"></div><script>(function(){const b=document.getElementById("mbtn"),x=document.getElementById("sbx"),ov=document.getElementById("sbov"),n=document.getElementById("sb"),R=document.documentElement,B=document.body;n.querySelectorAll("a[data-p]").forEach(a=>{if(a.dataset.p===(location.pathname.replace(/\/+$/,"")||"/"))a.className="cur"});let V={w:"1600",a:"c"},O=window.innerWidth>=1100;try{V=Object.assign(V,JSON.parse(localStorage.getItem("rokview")||"{}"));const o=localStorage.getItem("roksb");if(o!==null)O=o==="1"}catch(e){}const ap=()=>{R.style.setProperty("--mw",V.w==="100%"?"100%":V.w+"px");R.style.setProperty("--ml",V.a==="l"?"0":"auto");R.style.setProperty("--mr",V.a==="r"?"0":"auto");n.querySelectorAll("#mw button").forEach(y=>y.classList.toggle("on",y.dataset.v===V.w));n.querySelectorAll("#ma button").forEach(y=>y.classList.toggle("on",y.dataset.v===V.a))};const so=v=>{O=v;B.classList.toggle("sbopen",O);b.setAttribute("aria-expanded",O);try{localStorage.setItem("roksb",O?"1":"0")}catch(e){}};n.addEventListener("click",e=>{const y=e.target.closest(".seg button");if(!y)return;if(y.parentNode.id==="mw")V.w=y.dataset.v;else V.a=y.dataset.v;try{localStorage.setItem("rokview",JSON.stringify(V))}catch(e){}ap()});ap();B.classList.toggle("sbopen",O);b.onclick=()=>so(!O);x.onclick=()=>so(false);ov.onclick=()=>so(false);document.addEventListener("keydown",e=>{if(e.key==="Escape"&&window.innerWidth<1100)so(false)})})()</script>
<section class="hero"><div class="avc"><div class="av" id="avbox" tabindex="0" role="button" title="Avatar ändern"><span id="hav">⚔</span></div><button id="avreset" hidden>Avatar zurücksetzen</button><input type="file" id="avfile" accept="image/*" hidden></div><div>
<div class="hrow"><div><h1 id="hname">Barbaren-Tracker</h1><div id="per"></div><div id="st"></div></div>
<div class="bar"><select id="acc" hidden></select><button id="mainbtn" hidden></button><select id="range">
<option value="today">Heute</option><option value="yesterday">Gestern</option><option value="7">Letzte 7 Tage</option>
<option value="30">Letzte 30 Tage</option><option value="month">Dieser Monat</option><option value="all">Alles</option>
<option value="custom">Zeitraum wählen</option></select>
<span id="cu" hidden><label>von <input type="date" id="d1"></label> <label>bis <input type="date" id="d2"></label></span>
<button id="expand">Alle Berichte aufklappen</button><button id="collapse">zuklappen</button></div></div>
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
const HASHTAB={profil:'pf',loot:'loot',beute:'loot',forts:'ft',festungen:'ft',barbaren:'bt',pvp:'pv',aoo:'ao',tag:'dt',namen:'nm'};
if(location.hash.length>1&&HASHTAB[location.hash.slice(1).toLowerCase()])tab=HASHTAB[location.hash.slice(1).toLowerCase()];
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
 $('hname').textContent=nm+(pa&&pa.account===s.main_account?' ★':'');
 const avKey=pa?pa.account:'all',avV=(s.avatars||{})[avKey];window.avKey=avKey;$('avreset').hidden=!avV;
 if(avV){if($('hav').dataset.v!==avKey+avV){$('hav').innerHTML=`<img src="/avatar/${avKey}?v=${avV}" alt="">`;$('hav').dataset.v=avKey+avV}}
 else{$('hav').dataset.v='';$('hav').textContent=nm.normalize('NFKD').replace(/[^A-Za-z0-9]/g,'').slice(0,2).toUpperCase()||'⚔'}
 const pr=s.profile.find(x=>pa&&x.account===pa.account)||(s.profile.length===1&&!(s.accounts.length>1&&!pa)?s.profile[0]:null);
 $('hchips').innerHTML=pr?[['power','⚡','Macht'],['kill_points','⚔️','Kill Points'],['dead','💀','Tote'],['helps','🤝','Hilfen'],['gathered','🧺','Gesammelt']].filter(x=>pr.last[x[0]]!=null)
   .map(x=>`<div class="chip"><b>${x[1]} ${compact(pr.last[x[0]])}</b><span>${x[2]}${pr.delta[x[0]]?`<em class="${pr.delta[x[0]]>=0?'good':'bad'}">${dd(pr.delta[x[0]])}</em>`:''}</span></div>`).join(''):'';
 showTab(tab);
 $('dt').innerHTML='<tr><th>Tag</th><th>Solo-Berichte</th><th>Rallys</th><th>Festungen</th><th>Gegner (solo)</th><th>Barbaren-Truppen (solo)</th><th>Größter Gegner</th><th>XP</th><th>Verwundet (solo)</th><th>Spielerkampf-Berichte</th></tr>'+
  s.days.filter(d=>d.day>=a&&d.day<=b).map(d=>`<tr><td>${dm(d.day)}</td><td>${f(d.solo)}</td><td>${f(d.rallies)}</td><td>${f(d.forts)}</td><td>${f(d.foes)}</td><td>${f(d.killed)}</td><td>${f(d.maxfoe)}</td><td>${f(d.xp)}</td><td>${f(d.hurt)}</td><td>${f(d.pvp)}</td></tr>`).join('')}
const avPost=async data=>{await fetch('/api/avatar',{method:'POST',body:JSON.stringify({key:window.avKey||'all',data})});lastSig='';load()};
$('avbox').onclick=()=>$('avfile').click();$('avbox').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('avfile').click()}};
$('avreset').onclick=()=>avPost('');
$('avfile').onchange=e=>{const fl=e.target.files[0];e.target.value='';if(!fl)return;const im=new Image(),u=URL.createObjectURL(fl);
 im.onload=()=>{const S=256,c=document.createElement('canvas');c.width=c.height=S;const k=Math.max(S/im.width,S/im.height),w=im.width*k,h=im.height*k;
  c.getContext('2d').drawImage(im,(S-w)/2,(S-h)/2,w,h);URL.revokeObjectURL(u);let d=c.toDataURL('image/webp',.9);if(!d.startsWith('data:image/webp'))d=c.toDataURL('image/jpeg',.9);avPost(d)};
 im.onerror=()=>{URL.revokeObjectURL(u);alert('Bild konnte nicht gelesen werden.')};im.src=u};
$('mainbtn').onclick=async()=>{const a=$('acc').value;if(!a)return;const cur=lastMain;await fetch('/api/main',{method:'POST',body:JSON.stringify({account:cur===a?'':a})});lastSig='';load()};
$('pbtn').onclick=()=>$('pfile').click();
$('pfile').onchange=async e=>{let n=0,err='';for(const fl of e.target.files){try{const r=await fetch('/api/profile',{method:'POST',body:await fl.text()}).then(r=>r.json());if(r.ok)n+=r.n;else err=r.error}catch(x){err=String(x)}}
 $('pfmsg').textContent=err?'Fehler: '+err:n+' Eintrag/Einträge übernommen.';e.target.value='';lastSig='';load()};
['range','d1','d2','acc'].forEach(i=>$(i).onchange=()=>{lastSig='';load()});load();setInterval(load,4000);
// ===== Sprache: Deutsch / English (RoK-Begriffe) =====
const EN=[
[`Barbaren-Tracker`,`Barbarian Tracker`],[`Heute`,`Today`],[`Gestern`,`Yesterday`],[`Letzte 7 Tage`,`Last 7 days`],[`Letzte 30 Tage`,`Last 30 days`],
[`Dieser Monat`,`This month`],[`Alles`,`All time`],[`Zeitraum wählen`,`Custom range`],[`von`,`from`],[`bis`,`to`],[`und`,`and`],
[`Allianz-Tracker`,`Alliance Tracker`],[`Zum Loot-Tracker`,`Back to loot tracker`],[`Loot-Tracker`,`Loot tracker`],[`Sprache`,`Language`],[`Seiten`,`Pages`],[`Ansicht`,`View`],[`Breite`,`Width`],[`Ausrichtung`,`Alignment`],[`Schmal`,`Narrow`],[`Breit`,`Wide`],[`Voll`,`Full`],[`Links`,`Left`],[`Mitte`,`Center`],[`Rechts`,`Right`],[`Allianz-Ranking`,`Alliance ranking`],[`Member-Rangliste`,`Member ranking`],[`Mitglieder`,`members`],
[`Klick auf eine Spaltenüberschrift sortiert die Liste. Grün und rot zeigen die Veränderung seit dem vorherigen Stand. Ranglisten ohne Resource Assistance und Armory Contributions.`,`Click a column header to sort the list. Green and red show the change since the previous snapshot. Rankings without Resource Assistance and Armory Contributions.`],
[`Ø Rang`,`Avg rank`],[`Durchschnitt der Plätze in den eingeschalteten Kategorien, niedriger ist besser`,`Average of the ranks in the categories that are on, lower is better`],
[`Klick auf eine Kategorie über der Tabelle schaltet sie aus, mit der Maus ziehen ändert die Reihenfolge der Spalten. Die Einstellungen werden gespeichert. Eine ausgeschaltete Kategorie wird ausgeblendet und zählt nicht zum Ø Rang. Ø Rang ist der Durchschnitt der Plätze in den eingeschalteten Kategorien, niedriger ist besser. Die Zahl dahinter ist der Platz in dieser Durchschnittswertung.`,`Click a category above the table to switch it off, drag it with the mouse to change the column order. The settings are saved. A switched-off category is hidden and does not count toward the avg rank. Avg rank is the average of the ranks in the categories that are on, lower is better. The number after it is the position in that average ranking.`],
[`Als HTML exportieren`,`Export as HTML`],[`Speichert die Rangliste als eigenständige Datei, die du weitergeben kannst`,`Saves the ranking as a standalone file you can share`],[`Allianz-Daten einfügen …`,`Import alliance data …`],[`Mitglied suchen …`,`Search member …`],[`Allianz`,`Alliance`],
[`Ranglisten deiner Allianz aus Screenshots (ohne Resource Assistance und Armory Contributions). Die Datei, die ich dir schicke, fügst du hier ein. Frühere Stände bleiben gespeichert, so siehst du, wer wie viel dazugewonnen hat.`,`Rankings of your alliance from screenshots (without Resource Assistance and Armory Contributions). Import the file I send you here. Earlier snapshots are kept, so you can see who gained how much.`],
[`Noch keine Allianz-Daten. Schick mir Screenshots der Ranglisten, ich erstelle dir die Datei zum Einfügen.`,`No alliance data yet. Send me screenshots of the rankings and I will create the file to import.`],
[`Nicht mehr in der Liste, zuletzt gesehen`,`No longer listed, last seen`],[`Klick auf einen Namen zeigt den Verlauf.`,`Click a name to see the history.`],[`Noch keine Daten`,`No data yet`],
[`Mitgliedern`,`members`],[`Rang`,`Rank`],[`Wert`,`Value`],[`Veränderung`,`Change`],[`Avatar ändern`,`Change avatar`],[`Avatar zurücksetzen`,`Reset avatar`],[`Alle Berichte aufklappen`,`Expand all reports`],[`zuklappen`,`Collapse all`],
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
 if(n.nodeType===3){if(n.parentNode&&n.parentNode.closest&&n.parentNode.closest('.nm'))return;const v=enText(n.nodeValue);if(v!==n.nodeValue)n.nodeValue=v;return}
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
$('lang').value=LANG;
$('lang').onchange=()=>{try{localStorage.setItem('roklang',$('lang').value)}catch(e){}location.reload()};
</script></body></html>"""

# ---------------------------------------------------------------- Eigene Seite: Allianz-Tracker (nur die Member-Liste)
_STYLE = PAGE[PAGE.index("<style>"):PAGE.index("</style>") + 8]
_I18N = PAGE[PAGE.index("// ===== Sprache"):PAGE.index("</script>", PAGE.index("// ===== Sprache"))]
ALLIANCE_PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Allianz-Tracker</title>
""" + _STYLE + r"""
<style>
.ah{display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between;padding:20px 22px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(135deg,var(--soft),var(--card));margin-bottom:14px;box-shadow:0 18px 40px -26px rgba(0,0,0,.7)}
.ah h1{margin:0}.ah .sub{color:var(--mute);font-size:.9rem;margin-top:2px}
a.btn{font:inherit;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:7px 12px;text-decoration:none}a.btn:hover{border-color:var(--acc)}
#achips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.chip{font:inherit;font-size:.85rem;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:5px 12px;cursor:pointer}.chip[aria-pressed="false"]{color:var(--mute);text-decoration:line-through;background:transparent}.chip[aria-pressed="true"]{border-color:var(--acc)}
#al th,#al td{padding:9px 12px}#al td.r{color:var(--mute);width:2.5em;text-align:right}#al td.n{text-align:left}
</style></head><body><main>
<button type="button" id="mbtn" class="mbtn" aria-label="Menü" aria-expanded="true" title="Menü">☰</button><aside id="sb" class="sb" aria-label="Menü"><div class="sbh"><span class="sbt">RoK&nbsp;Tracker</span><button type="button" id="sbx" class="mbtn" aria-label="Menü schließen" title="Menü schließen">⇤</button></div><nav class="sbn"><div class="sbl">Seiten</div><a href="/" data-p="/"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4v5c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V7z"/><path d="M9 12l2 2 4-4"/></svg><span>Loot-Tracker</span></a><a href="/allianz" data-p="/allianz"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg><span>Allianz-Ranking</span></a><div class="msep"></div><div class="sbl">Ansicht</div><label class="mrow"><span>Sprache</span><select id="lang" aria-label="Language / Sprache"><option value="de">🌐 Deutsch</option><option value="en">🌐 English</option></select></label><div class="mrow"><span>Breite</span><span class="seg" id="mw"><button type="button" data-v="900">Schmal</button><button type="button" data-v="1160">Normal</button><button type="button" data-v="1600">Breit</button><button type="button" data-v="100%">Voll</button></span></div><div class="mrow"><span>Ausrichtung</span><span class="seg" id="ma"><button type="button" data-v="l">Links</button><button type="button" data-v="c">Mitte</button><button type="button" data-v="r">Rechts</button></span></div></nav></aside><div id="sbov" class="sbov"></div><script>(function(){const b=document.getElementById("mbtn"),x=document.getElementById("sbx"),ov=document.getElementById("sbov"),n=document.getElementById("sb"),R=document.documentElement,B=document.body;n.querySelectorAll("a[data-p]").forEach(a=>{if(a.dataset.p===(location.pathname.replace(/\/+$/,"")||"/"))a.className="cur"});let V={w:"1600",a:"c"},O=window.innerWidth>=1100;try{V=Object.assign(V,JSON.parse(localStorage.getItem("rokview")||"{}"));const o=localStorage.getItem("roksb");if(o!==null)O=o==="1"}catch(e){}const ap=()=>{R.style.setProperty("--mw",V.w==="100%"?"100%":V.w+"px");R.style.setProperty("--ml",V.a==="l"?"0":"auto");R.style.setProperty("--mr",V.a==="r"?"0":"auto");n.querySelectorAll("#mw button").forEach(y=>y.classList.toggle("on",y.dataset.v===V.w));n.querySelectorAll("#ma button").forEach(y=>y.classList.toggle("on",y.dataset.v===V.a))};const so=v=>{O=v;B.classList.toggle("sbopen",O);b.setAttribute("aria-expanded",O);try{localStorage.setItem("roksb",O?"1":"0")}catch(e){}};n.addEventListener("click",e=>{const y=e.target.closest(".seg button");if(!y)return;if(y.parentNode.id==="mw")V.w=y.dataset.v;else V.a=y.dataset.v;try{localStorage.setItem("rokview",JSON.stringify(V))}catch(e){}ap()});ap();B.classList.toggle("sbopen",O);b.onclick=()=>so(!O);x.onclick=()=>so(false);ov.onclick=()=>so(false);document.addEventListener("keydown",e=>{if(e.key==="Escape"&&window.innerWidth<1100)so(false)})})()</script>
<section class="ah"><div><h1>Allianz-Tracker</h1><div class="sub" id="asub">Member-Rangliste</div></div>
<div class="bar"><a class="btn" id="aexp" href="/allianz/export?lang=de" title="Speichert die Rangliste als eigenständige Datei, die du weitergeben kannst">Als HTML exportieren</a><button id="abtn">Allianz-Daten einfügen …</button><input type="file" id="afile" accept=".json" multiple hidden></div></section>
<div class="pane" style="margin-top:0"><div class="chips" id="achips" role="group" aria-label="Kategorien im Durchschnitt"></div><div class="nmbar"><input id="asearch" type="search" placeholder="Mitglied suchen …"> <span class="note" id="ainfo"></span> <span class="note" id="amsg"></span></div>
<div class="wrap"><table id="al"></table></div>
<p class="note">Klick auf eine Spaltenüberschrift sortiert die Liste. Grün und rot zeigen die Veränderung seit dem vorherigen Stand. Ranglisten ohne Resource Assistance und Armory Contributions.</p>
<p class="note">Klick auf eine Kategorie über der Tabelle schaltet sie aus, mit der Maus ziehen ändert die Reihenfolge der Spalten. Die Einstellungen werden gespeichert. Eine ausgeschaltete Kategorie wird ausgeblendet und zählt nicht zum Ø Rang. Ø Rang ist der Durchschnitt der Plätze in den eingeschalteten Kategorien, niedriger ist besser. Die Zahl dahinter ist der Platz in dieser Durchschnittswertung.</p></div>
</main><script>
let LANG='de';try{LANG=localStorage.getItem('roklang')==='en'?'en':'de'}catch(e){}
const LOC=LANG==='en'?'en-US':'de-DE',$=id=>document.getElementById(id),f=n=>Number(n).toLocaleString(LOC);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dm=d=>LANG==='en'?(p=>p.length===3?+p[2]+' '+MON[+p[1]-1]+' '+p[0]:d)(d.split('-')):d.split('-').reverse().join('.');
function saveUi(){if(!lastAl)return;fetch('/api/alliance_ui',{method:'POST',body:JSON.stringify({order:lastAl.cats.map(c=>c.key),off:lastAl.cats.map(c=>c.key).filter(k=>!alOn.has(k)),sort:alSort,dir:alDir})}).catch(()=>{})}
let lastAl=null,lastSig='',alSort='power',alDir=-1,alQ='',alOn=null;const alOpen=new Set();
function renderAlliance(a){lastAl=a;if(!a)return;const cats=a.cats,ms=a.members;
 const newest=Object.values(a.latest||{}).sort().pop();
 $('asub').textContent=newest?'Member-Rangliste · Stand '+dm(newest):'Member-Rangliste';
 const AV=[['avg_all','Ø Rang',[]]];
  if(alOn===null){const u=a.ui||{};alOn=new Set(cats.map(c=>c.key).filter(k=>!(u.off||[]).includes(k)));
   if(u.sort&&(u.sort==='avg_all'||cats.some(c=>c.key===u.sort))){alSort=u.sort;alDir=u.dir||(u.sort==='avg_all'?1:-1)}}
  const vc=cats.filter(c=>alOn.has(c.key));if(alSort!=='avg_all'&&!alOn.has(alSort)){alSort='avg_all';alDir=1}
  $('achips').innerHTML=cats.map(c=>`<button type="button" class="chip" draggable="true" data-k="${c.key}" aria-pressed="${alOn.has(c.key)}">${c.label}</button>`).join('');
  const avg=(m,ex)=>{const r=vc.filter(c=>!ex.includes(c.key)).map(c=>m.c[c.key]&&m.c[c.key].r).filter(v=>v!=null);return r.length?r.reduce((x,y)=>x+y,0)/r.length:null};
  ms.forEach(m=>{m.a={};AV.forEach(([k,,ex])=>m.a[k]=avg(m,ex))});
  ms.forEach(m=>m.ap={});AV.forEach(([k])=>{const o=ms.filter(m=>m.a[k]!=null).map(m=>m.a[k]).sort((x,y)=>x-y);ms.forEach(m=>{m.ap[k]=m.a[k]==null?null:o.indexOf(m.a[k])+1})});
  const q=alQ.trim().toLowerCase(),val=(m,k)=>{if(k.startsWith('avg_'))return m.a[k];const x=m.c[k];return x&&x.v!=null?x.v:null};
  const acell=(m,k)=>m.a[k]==null?'<td>–</td>':`<td>${m.a[k].toFixed(1).replace('.',LANG==='en'?'.':',')}<span class="rk">#${m.ap[k]}</span></td>`;
 const rows=ms.filter(m=>!q||m.name.toLowerCase().includes(q));
 rows.sort((x,y)=>{const a1=val(x,alSort),b1=val(y,alSort);if(a1==null&&b1==null)return x.name.localeCompare(y.name);if(a1==null)return 1;if(b1==null)return -1;return (a1-b1)*alDir||x.name.localeCompare(y.name)});
 const dl=x=>x&&x.d!=null&&x.d!==0?`<span class="dl ${x.d>0?'good':'bad'}">${(x.d>0?'+':'')+f(x.d)}</span>`:'';
 const cell=(m,k)=>{const x=m.c[k];if(!x)return '<td>–</td>';if(x.v==null&&x.seen)return `<td class="note" title="Nicht mehr in der Liste, zuletzt gesehen ${dm(x.seen)}">–</td>`;
  return `<td>${x.v==null?'–':f(x.v)}${x.r!=null?`<span class="rk">#${x.r}</span>`:''}${dl(x)}</td>`};
 const hist=m=>`<tr class="det"><td colspan="${vc.length+3}"><div class="hist">${cats.filter(c=>m.h[c.key]).map(c=>{const h=m.h[c.key].slice().reverse();
   return `<div><h3>${c.label}</h3><table><tr><th>Tag</th><th>Wert</th><th>Rang</th><th>Veränderung</th></tr>${h.map((r,i)=>{const p=h[i+1],d=p&&p[1]!=null&&r[1]!=null?r[1]-p[1]:null;
     return `<tr><td>${dm(r[0])}</td><td>${r[1]==null?'–':f(r[1])}</td><td>${r[2]==null?'–':'#'+r[2]}</td><td class="${d>0?'good':d<0?'bad':''}">${d==null?'–':(d>0?'+':'')+f(d)}</td></tr>`}).join('')}</table></div>`}).join('')}</div></td></tr>`;
 $('al').innerHTML=ms.length?`<tr><th class="t">#</th><th class="t">Name</th>${AV.map(([k,l])=>`<th data-k="${k}" class="${alSort===k?'on':''}" title="Durchschnitt der Plätze in den eingeschalteten Kategorien, niedriger ist besser">${l}${alSort===k?(alDir<0?' ▼':' ▲'):''}</th>`).join('')}${vc.map(c=>`<th data-k="${c.key}" class="${alSort===c.key?'on':''}">${c.label}${alSort===c.key?(alDir<0?' ▼':' ▲'):''}</th>`).join('')}</tr>`+
  rows.map((m,i)=>`<tr class="m" data-n="${esc(m.name)}"><td class="r">${i+1}</td><td class="n nm">${alOpen.has(m.name)?'▾':'▸'} ${esc(m.name)}</td>${AV.map(([k])=>acell(m,k)).join('')}${vc.map(c=>cell(m,c.key)).join('')}</tr>${alOpen.has(m.name)?hist(m):''}`).join(''):
  '<tr><td class="note">Noch keine Allianz-Daten. Schick mir Screenshots der Ranglisten, ich erstelle dir die Datei zum Einfügen.</td></tr>';
 $('ainfo').textContent=ms.length?`${rows.length} / ${ms.length} Mitglieder`:''}
$('al').addEventListener('click',e=>{const th=e.target.closest('th[data-k]');if(th){if(alSort===th.dataset.k)alDir=-alDir;else{alSort=th.dataset.k;alDir=alSort.startsWith('avg_')?1:-1}saveUi();return renderAlliance(lastAl)}
 const tr=e.target.closest('tr.m');if(tr){const n=tr.dataset.n;alOpen.has(n)?alOpen.delete(n):alOpen.add(n);renderAlliance(lastAl)}});
$('asearch').oninput=()=>{alQ=$('asearch').value;renderAlliance(lastAl)};
$('achips').addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b||!alOn)return;const k=b.dataset.k;alOn.has(k)?alOn.delete(k):alOn.add(k);saveUi();renderAlliance(lastAl)});
let dragK=null;$('achips').addEventListener('dragstart',e=>{const b=e.target.closest('.chip');if(!b)return;dragK=b.dataset.k;e.dataTransfer.effectAllowed='move';try{e.dataTransfer.setData('text/plain',dragK)}catch(x){}});
$('achips').addEventListener('dragover',e=>{if(dragK&&e.target.closest('.chip'))e.preventDefault()});
$('achips').addEventListener('drop',e=>{const t=e.target.closest('.chip');if(!t||!dragK||!lastAl)return;e.preventDefault();const to=t.dataset.k;if(to===dragK){dragK=null;return}
 const cs=lastAl.cats.slice(),from=cs.findIndex(c=>c.key===dragK),at=cs.findIndex(c=>c.key===to);const [m]=cs.splice(from,1);cs.splice(at,0,m);lastAl.cats=cs;dragK=null;saveUi();renderAlliance(lastAl)});
$('abtn').onclick=()=>$('afile').click();
$('aexp').href='/allianz/export?lang='+LANG;
$('afile').onchange=async e=>{let n=0,err='';for(const fl of e.target.files){try{const r=await fetch('/api/alliance',{method:'POST',body:await fl.text()}).then(r=>r.json());if(r.ok)n+=r.n;else err=r.error}catch(x){err=String(x)}}
 $('amsg').textContent=err?'Fehler: '+err:n+' Eintrag/Einträge übernommen.';e.target.value='';lastSig='';load()};
async function load(){try{const a=await fetch('/api/alliance').then(r=>r.json());const sig=JSON.stringify(a);if(sig===lastSig)return;lastSig=sig;renderAlliance(a)}catch(e){}}
load();setInterval(load,4000);
""" + _I18N + r"""</script></body></html>"""

EXPORT_TPL = r"""<!doctype html>
<html lang="@@lang@@"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@title@@</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>
:root{--bg:#eef2f8;--card:#ffffff;--ink:#16213a;--mute:#5d6b86;--line:#d9e1ee;--soft:#f1f5fb;--acc:#2f6fdb;--head:#f6f9fd}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0a0f1a;--card:#111a2b;--ink:#e7edf8;--mute:#93a1bd;--line:#223049;--soft:#16233a;--acc:#6aa2ff;--head:#0f1828;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0a0f1a;--card:#111a2b;--ink:#e7edf8;--mute:#93a1bd;--line:#223049;--soft:#16233a;--acc:#6aa2ff;--head:#0f1828;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:14px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;padding-block:24px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}
h1{margin:0;font-size:1.6rem;letter-spacing:-.01em;text-wrap:balance}
.sub{color:var(--mute)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
.bar{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center}
input{font:inherit;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:0;width:260px;max-width:100%}
input:focus-visible,th:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.note{color:var(--mute);font-size:.85rem;margin:0}
.wrap{overflow:auto;max-height:78vh;border:1px solid var(--line);border-radius:12px}
table{border-collapse:separate;border-spacing:0;width:100%;min-width:1000px;font-variant-numeric:tabular-nums}
th,td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line);background:var(--card)}
thead th{position:sticky;top:0;background:var(--head);color:var(--mute);font-weight:600;font-size:.8rem;cursor:pointer;user-select:none;z-index:2}
thead th.on{color:var(--ink)}
th.t,td.n,td.r{text-align:left}
td.r{color:var(--mute);width:2.5em}
td.n{font-weight:600;position:sticky;left:0;z-index:1;box-shadow:1px 0 0 var(--line)}
thead th.n{left:0;z-index:3}
td.avg{background:var(--soft)}
.rk{color:var(--mute);font-size:.75rem;margin-left:6px}
tbody tr:hover td{background:var(--soft)}
@media (max-width:600px){h1{font-size:1.3rem}.panel{padding:12px}}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{font:inherit;font-size:.85rem;color:var(--ink);background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:5px 12px;cursor:pointer}
.chip[aria-pressed="false"]{color:var(--mute);text-decoration:line-through;background:transparent}
.chip[aria-pressed="true"]{border-color:var(--acc)}
.chip:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
</style>
<main>
<header><h1>@@title@@</h1><div class="sub">@@asof@@ · <span id="cnt"></span> @@members@@</div></header>
<section class="panel">
<div class="chips" id="chips" role="group" aria-label="@@cl@@"></div>
<div class="bar"><input id="q" type="search" placeholder="@@search@@" aria-label="@@search@@"><span class="note" id="info"></span></div>
<div class="wrap"><table id="t"></table></div>
<p class="note">@@note@@</p>
</section>
</main>
<script>
const CATS=@@CATS@@;
const DATA=@@DATA@@;
const fmt=new Intl.NumberFormat('@@loc@@'),dec=new Intl.NumberFormat('@@loc@@',{minimumFractionDigits:1,maximumFractionDigits:1});
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const on=new Set(CATS.map(c=>c[0]));
let sort='avg',dir=1,q='';
function calc(){
 DATA.forEach(m=>{const r=CATS.filter(c=>on.has(c[0])).map(c=>m.c[c[0]]&&m.c[c[0]][0]).filter(v=>v!=null);m.a=r.length?r.reduce((x,y)=>x+y,0)/r.length:null});
 const o=DATA.map(m=>m.a).filter(v=>v!=null).sort((x,y)=>x-y);
 DATA.forEach(m=>m.p=m.a==null?null:o.indexOf(m.a)+1);
}
const val=(m,k)=>k==='avg'?m.a:(m.c[k]?m.c[k][1]:null);
function chips(){
 document.getElementById('chips').innerHTML=CATS.map(c=>`<button class="chip" type="button" data-k="${c[0]}" aria-pressed="${on.has(c[0])}">${c[1]}</button>`).join('');
}
function render(){
 calc();
 if(sort!=='avg'&&!on.has(sort)){sort='avg';dir=1}
 const vis=CATS.filter(c=>on.has(c[0]));
 const rows=DATA.filter(m=>!q||m.n.toLowerCase().includes(q));
 rows.sort((x,y)=>{const a=val(x,sort),b=val(y,sort);if(a==null&&b==null)return x.n.localeCompare(y.n);if(a==null)return 1;if(b==null)return -1;return (a-b)*dir||x.n.localeCompare(y.n)});
 const arrow=k=>sort===k?(dir<0?' ▼':' ▲'):'';
 const th=(k,l)=>`<th data-k="${k}" tabindex="0" class="${sort===k?'on':''}">${l}${arrow(k)}</th>`;
 document.getElementById('t').innerHTML='<thead><tr><th class="t">#</th><th class="t n">@@name@@</th>'+th('avg','@@avg@@')+vis.map(c=>th(c[0],c[1])).join('')+'</tr></thead><tbody>'+
 rows.map((m,i)=>`<tr><td class="r">${i+1}</td><td class="n">${esc(m.n)}</td><td class="avg">${m.a==null?'–':dec.format(m.a)+'<span class="rk">#'+m.p+'</span>'}</td>`+vis.map(c=>{const x=m.c[c[0]];return x?`<td>${x[1]==null?'–':fmt.format(x[1])}<span class="rk">#${x[0]}</span></td>`:'<td>–</td>'}).join('')+'</tr>').join('')+'</tbody>';
 document.getElementById('info').textContent=rows.length+' / '+DATA.length;
 document.querySelectorAll('.chip').forEach(b=>b.setAttribute('aria-pressed',on.has(b.dataset.k)));
}
document.getElementById('cnt').textContent=DATA.length;
const sortBy=th=>{if(!th)return;const k=th.dataset.k;if(!k)return;if(sort===k)dir=-dir;else{sort=k;dir=k==='avg'?1:-1}render()};
document.getElementById('t').addEventListener('click',e=>sortBy(e.target.closest('th')));
document.getElementById('t').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sortBy(e.target.closest('th'))}});
document.getElementById('chips').addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b)return;const k=b.dataset.k;on.has(k)?on.delete(k):on.add(k);render()});
document.getElementById('q').oninput=e=>{q=e.target.value.trim().toLowerCase();render()};
chips();render();
</script>

</html>
"""
EXPORT_TXT = {
    "de": dict(title="Allianz-Rangliste", asof="Stand %s", members="Mitglieder", cl="Kategorien im Durchschnitt", search="Mitglied suchen …", name="Name", avg="Ø Rang", loc="de-DE", lang="de",
               note="Klick auf eine Kategorie oben schaltet sie aus. Eine ausgeschaltete Kategorie wird ausgeblendet und zählt nicht zum Durchschnitt. Klick auf eine Spaltenüberschrift sortiert die Liste. Hinter jedem Wert steht der Platz in dieser Kategorie. Ø Rang ist der Durchschnitt der Plätze in den eingeschalteten Kategorien, niedriger ist besser."),
    "en": dict(title="Alliance Ranking", asof="As of %s", members="members", cl="Categories included in the average", search="Search member …", name="Name", avg="Avg rank", loc="en-US", lang="en",
               note="Click a category above to switch it off. A switched-off category is hidden and does not count toward the average. Click a column header to sort. The number after each value is the rank in that category. Avg rank is the average of the ranks in the categories that are on, lower is better."),
}

def alliance_export_html(db, lang):
    """Eigenstaendige HTML-Datei mit dem neuesten Stand der Allianz-Rangliste (ohne Tracker, ohne Internet nutzbar)."""
    lang = "en" if lang == "en" else "de"
    a = db.alliance_stats()
    cats = [[c["key"], c["label"]] for c in a["cats"]]
    data = []
    for m in a["members"]:
        c = {k: [v["r"], v["v"]] for k, v in m["c"].items() if v.get("r") is not None}
        if c:
            data.append({"n": m["name"], "c": c})
    newest = max(a["latest"].values()) if a["latest"] else ""
    day = newest.split("-")
    if len(day) == 3:
        MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        stand = "%s.%s.%s" % (day[2], day[1], day[0]) if lang == "de" else "%d %s %s" % (int(day[2]), MON[int(day[1]) - 1], day[0])
    else:
        stand = "-"
    t = dict(EXPORT_TXT[lang])
    t["asof"] = t["asof"] % stand
    h = EXPORT_TPL
    for k, v in t.items():
        h = h.replace("@@%s@@" % k, v)
    js = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return h.replace("@@CATS@@", js(cats)).replace("@@DATA@@", js(data))


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
            elif u.path in ("/allianz", "/allianz/"):
                self.send(ALLIANCE_PAGE, "text/html")
            elif u.path == "/allianz/export":
                lang = parse_qs(u.query).get("lang", ["de"])[0]
                data = alliance_export_html(db, lang).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % ("Alliance-Ranking.html" if lang == "en" else "Allianz-Rangliste.html"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/api/alliance":
                self.send(json.dumps(db.alliance_stats()), "application/json")
            elif u.path == "/api/stats":
                q = parse_qs(u.query)
                self.send(json.dumps(db.stats(q.get("from", ["0000-01-01"])[0], q.get("to", ["9999-12-31"])[0],
                                              q.get("account", [""])[0])), "application/json")
            elif u.path.startswith("/avatar/"):
                av = db.get_avatar(u.path[8:])
                if not av:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = base64.b64decode(av[1])
                self.send_response(200)
                self.send_header("Content-Type", av[0])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
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
            if path == "/api/avatar":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 1000000)))
                    db.set_avatar(str(d.get("key", "")), d.get("data", ""))
                    self.send(json.dumps({"ok": True}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path == "/api/quit":          # vom Starter (rok_start.pyw) genutzt, um eine laufende Version zu beenden
                org = self.headers.get("Origin")
                if org and not org.startswith("http://127.0.0.1"):
                    self.send(json.dumps({"ok": False}), "application/json")
                    return
                self.send(json.dumps({"ok": True}), "application/json")
                threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)), daemon=True).start()
                return
            if path == "/api/alliance":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 4000000)))
                    self.send(json.dumps({"ok": True, "n": db.add_alliance(d)}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path == "/api/profile":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    d = json.loads(self.rfile.read(min(n, 2000000)))
                    snaps = d.get("snapshots", []) if isinstance(d, dict) else d
                    self.send(json.dumps({"ok": True, "n": db.add_profile(snaps)}), "application/json")
                except Exception as e:
                    self.send(json.dumps({"ok": False, "error": str(e)}), "application/json")
                return
            if path == "/api/alliance_ui":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    self.send(json.dumps({"ok": True, "ui": db.set_alliance_ui(json.loads(self.rfile.read(min(n, 5000))))}), "application/json")
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
