#!/usr/bin/env python3
"""
Lager- & Umsatz-Tool (Admin-Webseite) — komplett getrennt vom Telegram-Bot.

Starten:
    ADMIN_PW="deinPasswort" python admin.py
    (oder einfach `python admin.py`, dann ist das Passwort "changeme" — bitte aendern!)

Auf Replit:
    In der Shell:  ADMIN_PW="deinPasswort" python admin/admin.py
    Replit zeigt dann eine Webview + oeffentliche URL (…replit.dev) — die in die Gruppe posten.

Speichert alles in admin/admin.db (eigene Datenbank, ruehrt den Bot nicht an).
Nur Python 3 noetig, keine zusaetzlichen Pakete.
"""
import os
import re
import sqlite3
import hashlib
import html
import time
import http.cookies
from datetime import date, datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# ------------------------------------------------------------------ Konfig
HERE = os.path.dirname(os.path.abspath(__file__))
# DB_PATH per Env ueberschreibbar (z.B. auf Railway ein Volume: DB_PATH=/data/admin.db)
DB_PATH = os.getenv("DB_PATH", os.path.join(HERE, "admin.db"))
PORT = int(os.getenv("PORT", "8080"))
ADMIN_PW = os.getenv("ADMIN_PW", "changeme")
# Schweizer Zeit (fuer "heute"); passt bei Bedarf den Offset an.
TZ = timezone(timedelta(hours=2))

US = [(7, 100), (21, 250), (35, 400)]
STD = [(10, 100), (30, 250), (50, 400)]

# (id, Name, Preisstaffel) — 1:1 aus eurer bot/config.py
PRODUCTS = [
    ("p10", "🇺🇸 Lemon Cherry Gelato", US),
    ("p11", "🇺🇸 Mega Cherry Gelato", US),
    ("p12", "🇺🇸 OG Kush", US),
    ("p9", "🍌 La Banana", STD),
    ("p3", "⛽🍋 Sour Diesel (Cali)", STD),
    ("p4", "🍋🥛 Lemon x Cereal Milk (Cali)", STD),
]
PROD_BY_ID = {p[0]: p for p in PRODUCTS}


# ------------------------------------------------------------------ DB
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS inventory (
            product_id TEXT PRIMARY KEY,
            grams REAL NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            grams REAL NOT NULL,
            price INTEGER NOT NULL,
            sale_date TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    for pid, _, _ in PRODUCTS:
        conn.execute(
            "INSERT OR IGNORE INTO inventory (product_id, grams) VALUES (?, 0)", (pid,)
        )
    conn.commit()
    conn.close()


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def get_inventory():
    conn = db()
    rows = {r["product_id"]: r["grams"] for r in conn.execute("SELECT * FROM inventory")}
    conn.close()
    return rows


def get_stats():
    conn = db()
    t = today_str()
    row = conn.execute(
        "SELECT COALESCE(SUM(price),0) r, COALESCE(SUM(grams),0) g, COUNT(*) c FROM sales"
    ).fetchone()
    rowt = conn.execute(
        "SELECT COALESCE(SUM(price),0) r, COALESCE(SUM(grams),0) g, COUNT(*) c FROM sales WHERE sale_date=?",
        (t,),
    ).fetchone()
    conn.close()
    return {
        "total_rev": row["r"], "total_g": row["g"], "total_c": row["c"],
        "today_rev": rowt["r"], "today_g": rowt["g"], "today_c": rowt["c"],
    }


def get_sales(limit=60):
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sales ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    )]
    conn.close()
    return rows


def get_by_day(limit=14):
    conn = db()
    rows = [dict(r) for r in conn.execute(
        """SELECT sale_date,
                  COALESCE(SUM(price),0) rev,
                  COALESCE(SUM(grams),0) g,
                  COUNT(*) c
           FROM sales GROUP BY sale_date ORDER BY sale_date DESC LIMIT ?""",
        (limit,),
    )]
    conn.close()
    return rows


def get_by_week(limit=8):
    conn = db()
    rows = [dict(r) for r in conn.execute(
        """SELECT strftime('%Y', sale_date) y,
                  strftime('%W', sale_date) w,
                  COALESCE(SUM(price),0) rev,
                  COALESCE(SUM(grams),0) g,
                  COUNT(*) c
           FROM sales GROUP BY y, w ORDER BY y DESC, w DESC LIMIT ?""",
        (limit,),
    )]
    conn.close()
    return rows


def add_sale(customer, pid, grams, price, sale_date):
    conn = db()
    name = PROD_BY_ID[pid][1]
    conn.execute(
        """INSERT INTO sales (customer, product_id, product_name, grams, price, sale_date, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (customer, pid, name, grams, price, sale_date, int(time.time())),
    )
    conn.execute(
        "UPDATE inventory SET grams = grams - ? WHERE product_id=?", (grams, pid)
    )
    conn.commit()
    conn.close()


def delete_sale(sale_id):
    conn = db()
    r = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if r:
        conn.execute(
            "UPDATE inventory SET grams = grams + ? WHERE product_id=?",
            (r["grams"], r["product_id"]),
        )
        conn.execute("DELETE FROM sales WHERE id=?", (sale_id,))
        conn.commit()
    conn.close()


def restock(pid, grams, mode):
    conn = db()
    if mode == "set":
        conn.execute("UPDATE inventory SET grams=? WHERE product_id=?", (grams, pid))
    else:  # add
        conn.execute(
            "UPDATE inventory SET grams = grams + ? WHERE product_id=?", (grams, pid)
        )
    conn.commit()
    conn.close()


# ------------------------------------------------------------------ HTML
def fmt_money(n):
    return f"{int(round(n)):,}".replace(",", "'") + ".-"


def fmt_g(n):
    n = float(n)
    s = f"{n:.0f}" if abs(n - round(n)) < 1e-9 else f"{n:.1f}"
    return s + "g"


CSS = """
:root{
  --bg:#0e1411; --surface:#161f1a; --surface2:#1e2a23; --line:#2a3a30;
  --text:#eaf0ea; --muted:#9fb0a4; --gold:#e8b451; --gold-dim:#8a6b2e;
  --good:#5bbf7a; --warn:#e0a336; --bad:#e2685f;
}
:root[data-theme="light"]{
  --bg:#f3f5f1; --surface:#ffffff; --surface2:#eef2ec; --line:#dbe4dc;
  --text:#16211a; --muted:#5c6d61; --gold:#a9791f; --gold-dim:#c9a24e;
  --good:#2e8b57; --warn:#a9791f; --bad:#c0443b;
}
@media (prefers-color-scheme:light){
  :root:not([data-theme="dark"]){
    --bg:#f3f5f1; --surface:#ffffff; --surface2:#eef2ec; --line:#dbe4dc;
    --text:#16211a; --muted:#5c6d61; --gold:#a9791f; --gold-dim:#c9a24e;
    --good:#2e8b57; --warn:#a9791f; --bad:#c0443b;
  }
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--bg); color:var(--text);
  font-family:'Chivo',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  line-height:1.5; -webkit-text-size-adjust:100%;
}
.wrap{max-width:640px;margin:0 auto;padding:16px 14px 60px}
.mono{font-family:'Chivo Mono',ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}
h1{font-size:19px;margin:0;letter-spacing:.2px}
.sub{color:var(--muted);font-size:12.5px;margin-top:2px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:1.3px;color:var(--muted);margin:0 0 10px}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat .lbl{font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted)}
.stat .big{font-size:30px;font-weight:700;margin-top:6px;color:var(--gold)}
.stat.total .big{color:var(--text)}
.stat .meta{font-size:12px;color:var(--muted);margin-top:4px}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 5px}
input,select{
  width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);
  background:var(--surface2);color:var(--text);font-size:16px;font-family:inherit
}
input:focus,select:focus{outline:2px solid var(--gold);outline-offset:1px}
.tiers{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.tier{flex:1 1 auto;min-width:96px;padding:11px 8px;border-radius:10px;border:1px solid var(--line);
  background:var(--surface2);color:var(--text);font-size:14px;cursor:pointer;text-align:center;font-family:inherit}
.tier.on{border-color:var(--gold);background:rgba(232,180,81,.14);color:var(--gold)}
.tier small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.tier.on small{color:var(--gold)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button.go{width:100%;margin-top:14px;padding:14px;border:0;border-radius:12px;background:var(--gold);
  color:#20160a;font-weight:700;font-size:16px;cursor:pointer;font-family:inherit}
button.go:active{transform:translateY(1px)}
.inv{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 0;border-bottom:1px solid var(--line)}
.inv:last-child{border-bottom:0}
.inv .nm{font-size:14px}
.inv .amt{font-size:17px;font-weight:700}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;margin-left:6px}
.ok{background:rgba(91,191,122,.16);color:var(--good)}
.low{background:rgba(224,163,54,.18);color:var(--warn)}
.out{background:rgba(226,104,95,.18);color:var(--bad)}
.sale{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)}
.sale:last-child{border-bottom:0}
.sale .l{min-width:0}
.sale .who{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sale .det{font-size:12px;color:var(--muted);margin-top:1px}
.sale .r{display:flex;align-items:center;gap:10px;flex:none}
.sale .pr{font-weight:700}
.del{background:none;border:1px solid var(--line);color:var(--muted);border-radius:8px;padding:6px 9px;font-size:13px;cursor:pointer}
.del:active{color:var(--bad);border-color:var(--bad)}
.muted{color:var(--muted)}
.flash{background:rgba(91,191,122,.14);border:1px solid var(--good);color:var(--good);
  padding:10px 12px;border-radius:10px;margin-bottom:12px;font-size:13.5px}
.warnbar{background:rgba(224,163,54,.12);border:1px solid var(--warn);color:var(--warn);
  padding:9px 12px;border-radius:10px;margin-bottom:12px;font-size:12.5px}
.tog{background:var(--surface);border:1px solid var(--line);color:var(--muted);border-radius:9px;
  padding:8px 10px;font-size:13px;cursor:pointer}
details summary{cursor:pointer;color:var(--muted);font-size:13px;list-style:none}
details summary::-webkit-details-marker{display:none}
.re{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:end;margin-top:10px}
.re label{margin:0 0 4px}
.re button{padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--surface2);
  color:var(--text);font-size:14px;cursor:pointer;font-family:inherit}
.line{border:1px solid var(--line);border-radius:12px;padding:10px;margin-bottom:8px;background:var(--surface2)}
.linetop{display:flex;gap:8px;align-items:center}
.linetop select{flex:1}
.rm{flex:none;background:none;border:1px solid var(--line);color:var(--muted);border-radius:8px;padding:9px 11px;cursor:pointer;font-size:14px}
.rm:active{color:var(--bad);border-color:var(--bad)}
.addline{width:100%;padding:11px;border:1px dashed var(--line);background:transparent;color:var(--muted);
  border-radius:10px;cursor:pointer;font-family:inherit;font-size:14px;margin:2px 0 10px}
.total{text-align:right;font-size:14px;margin:2px 0;color:var(--muted)}
.total span{color:var(--gold);font-weight:700;font-size:19px;margin-left:6px}
"""


def shell(title, body):
    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1'>"
        "<meta name='color-scheme' content='dark light'>"
        f"<title>{html.escape(title)}</title>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Chivo:wght@400;700;900&family=Chivo+Mono:wght@400;700&display=swap'>"
        f"<style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def login_page(err=""):
    e = f"<div class='warnbar'>{html.escape(err)}</div>" if err else ""
    body = (
        "<div class='top'><div><h1>🌿 Lager & Umsatz</h1>"
        "<div class='sub'>Bitte Passwort eingeben</div></div></div>"
        f"{e}"
        "<form method='post' action='/login' class='card'>"
        "<label>Passwort</label>"
        "<input type='password' name='pw' autofocus autocomplete='current-password'>"
        "<button class='go' type='submit'>Einloggen</button></form>"
    )
    return shell("Lager & Umsatz — Login", body)


def dashboard(flash=""):
    inv = get_inventory()
    st = get_stats()
    sales = get_sales()

    warn = ""
    if ADMIN_PW == "changeme":
        warn = ("<div class='warnbar'>⚠️ Standard-Passwort aktiv. Setze ein eigenes: "
                "Server mit <b>ADMIN_PW=\"deinPasswort\"</b> starten.</div>")

    flash_html = f"<div class='flash'>{html.escape(flash)}</div>" if flash else ""

    stats = (
        "<div class='stats'>"
        "<div class='stat'><div class='lbl'>Umsatz heute</div>"
        f"<div class='big mono'>{fmt_money(st['today_rev'])}</div>"
        f"<div class='meta'>{st['today_c']} Verkäufe · {fmt_g(st['today_g'])}</div></div>"
        "<div class='stat total'><div class='lbl'>💰 Cash Pot (gesamt)</div>"
        f"<div class='big mono'>{fmt_money(st['total_rev'])}</div>"
        f"<div class='meta'>{st['total_c']} Verkäufe · {fmt_g(st['total_g'])}</div></div>"
        "</div>"
    )

    # ---- Verkauf buchen (ein Kunde, mehrere Produktzeilen)
    opts = "".join(
        f"<option value='{pid}'>{html.escape(name)}</option>" for pid, name, _ in PRODUCTS
    )
    tiers_js = "{" + ",".join(
        "'%s':[%s]" % (pid, ",".join(f"[{g},{p}]" for g, p in tiers))
        for pid, _, tiers in PRODUCTS
    ) + "}"
    prod_js = "[" + ",".join(
        "['%s','%s']" % (pid, name.replace("\\", "\\\\").replace("'", "\\'"))
        for pid, name, _ in PRODUCTS
    ) + "]"
    sale_form = (
        "<div class='card'><h2>Verkauf buchen</h2>"
        "<form method='post' action='/sale' id='saleform'>"
        "<div class='row2'>"
        "<div><label>Kunde</label><input name='customer' placeholder='z.B. Kunde 1' autocomplete='off'></div>"
        f"<div><label>Datum</label><input type='date' name='sale_date' value='{today_str()}'></div>"
        "</div>"
        "<label>Produkte</label>"
        "<div id='lines'></div>"
        "<button type='button' class='addline' id='addline'>＋ weiteres Produkt</button>"
        "<div class='total'>Summe:<span id='total'>0.-</span></div>"
        "<button class='go' type='submit'>Verkauf buchen &amp; abziehen</button>"
        "</form></div>"
    )

    # ---- Lager
    inv_rows = ""
    for pid, name, _ in PRODUCTS:
        g = inv.get(pid, 0)
        if g <= 0:
            pill = "<span class='pill out'>leer</span>"
        elif g <= 20:
            pill = "<span class='pill low'>knapp</span>"
        else:
            pill = "<span class='pill ok'>ok</span>"
        inv_rows += (
            f"<div class='inv'><div class='nm'>{html.escape(name)}{pill}</div>"
            f"<div class='amt mono'>{fmt_g(g)}</div></div>"
        )
    restock_opts = "".join(
        f"<option value='{pid}'>{html.escape(name)}</option>" for pid, name, _ in PRODUCTS
    )
    inv_card = (
        "<div class='card'><h2>Lager / Pot</h2>"
        f"{inv_rows}"
        "<details style='margin-top:12px'><summary>➕ Lager auffüllen / setzen</summary>"
        "<form method='post' action='/restock' class='re'>"
        f"<div><label>Produkt</label><select name='product_id'>{restock_opts}</select></div>"
        "<div><label>Gramm</label><input class='mono' name='grams' inputmode='decimal' style='width:90px' placeholder='g'></div>"
        "<div><label>&nbsp;</label><button name='mode' value='add' type='submit'>+ dazu</button></div>"
        "</form>"
        "<form method='post' action='/restock' class='re' style='margin-top:6px'>"
        f"<div><label class='muted' style='font-size:11px'>oder Bestand exakt setzen</label><select name='product_id'>{restock_opts}</select></div>"
        "<div><input class='mono' name='grams' inputmode='decimal' style='width:90px' placeholder='g'></div>"
        "<div><button name='mode' value='set' type='submit'>= setzen</button></div>"
        "</form></details></div>"
    )

    # ---- Verkaeufe Liste
    if sales:
        rows = ""
        for s in sales:
            cust = html.escape(s["customer"] or "—")
            rows += (
                "<div class='sale'><div class='l'>"
                f"<div class='who'>{cust}</div>"
                f"<div class='det'>{html.escape(s['product_name'])} · {fmt_g(s['grams'])} · {html.escape(s['sale_date'])}</div>"
                "</div><div class='r'>"
                f"<div class='pr mono'>{fmt_money(s['price'])}</div>"
                f"<form method='post' action='/delete' onsubmit=\"return confirm('Verkauf löschen? Menge geht zurück ins Lager.')\">"
                f"<input type='hidden' name='id' value='{s['id']}'>"
                "<button class='del' type='submit'>✕</button></form>"
                "</div></div>"
            )
    else:
        rows = "<div class='muted' style='padding:8px 0'>Noch keine Verkäufe.</div>"
    sales_card = f"<div class='card'><h2>Verkäufe</h2>{rows}</div>"

    # ---- Pro Tag
    days = get_by_day()
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    if days:
        drows = ""
        for d in days:
            try:
                dt = datetime.strptime(d["sale_date"], "%Y-%m-%d")
                lbl = f"{wd[dt.weekday()]} {dt.strftime('%d.%m.')}"
            except ValueError:
                lbl = d["sale_date"]
            drows += (
                "<div class='inv'><div class='nm'>"
                f"{html.escape(lbl)} <span class='muted' style='font-size:12px'>· {d['c']} · {fmt_g(d['g'])}</span></div>"
                f"<div class='amt mono'>{fmt_money(d['rev'])}</div></div>"
            )
        day_card = f"<div class='card'><h2>Pro Tag</h2>{drows}</div>"
    else:
        day_card = ""

    # ---- Pro Woche
    weeks = get_by_week()
    if weeks:
        wrows = ""
        for w in weeks:
            lbl = f"KW {w['w']} · {w['y']}"
            wrows += (
                "<div class='inv'><div class='nm'>"
                f"{html.escape(lbl)} <span class='muted' style='font-size:12px'>· {w['c']} · {fmt_g(w['g'])}</span></div>"
                f"<div class='amt mono'>{fmt_money(w['rev'])}</div></div>"
            )
        week_card = f"<div class='card'><h2>Pro Woche</h2>{wrows}</div>"
    else:
        week_card = ""

    body = (
        "<div class='top'><div><h1>🌿 Lager &amp; Umsatz</h1>"
        f"<div class='sub'>Stand {html.escape(today_str())}</div></div>"
        "<button class='tog' onclick=\"var r=document.documentElement;"
        "var d=r.getAttribute('data-theme')==='light'?'dark':'light';"
        "r.setAttribute('data-theme',d);try{localStorage.setItem('th',d)}catch(e){}\">◐</button></div>"
        f"{warn}{flash_html}{stats}{sale_form}{inv_card}{day_card}{week_card}{sales_card}"
        "<script>"
        "try{var t=localStorage.getItem('th');if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}"
        f"var TIERS={tiers_js};var PRODS={prod_js};"
        "var linesEl=document.getElementById('lines'),totalEl=document.getElementById('total');"
        "function optsHtml(){return PRODS.map(function(p){return \"<option value='\"+p[0]+\"'>\"+p[1]+\"</option>\";}).join('');}"
        "function recalc(){var t=0;linesEl.querySelectorAll('.lp').forEach(function(i){var v=parseFloat(i.value);if(!isNaN(v))t+=v;});"
        "totalEl.textContent=(t?t.toLocaleString('de-CH').replace(/,/g,\"'\"):'0')+'.-';}"
        "function renderTiers(line){var box=line.querySelector('.tiers'),sel=line.querySelector('.lprod');"
        "box.innerHTML='';(TIERS[sel.value]||[]).forEach(function(t){var b=document.createElement('button');"
        "b.type='button';b.className='tier';b.innerHTML=t[0]+'g<small>'+t[1]+'.-</small>';"
        "b.onclick=function(){line.querySelector('.lg').value=t[0];line.querySelector('.lp').value=t[1];"
        "box.querySelectorAll('.tier').forEach(function(c){c.classList.remove('on');});b.classList.add('on');recalc();};"
        "box.appendChild(b);});}"
        "function makeLine(){var d=document.createElement('div');d.className='line';"
        "d.innerHTML=\"<div class='linetop'><select class='lprod' name='line_product'>\"+optsHtml()+\"</select>\"+"
        "\"<button type='button' class='rm' title='Zeile entfernen'>✕</button></div>\"+"
        "\"<div class='tiers'></div><div class='row2' style='margin-top:8px'>\"+"
        "\"<input class='mono lg' name='line_grams' inputmode='decimal' placeholder='Gramm'>\"+"
        "\"<input class='mono lp' name='line_price' inputmode='numeric' placeholder='Preis .-'></div>\";"
        "d.querySelector('.lprod').addEventListener('change',function(){d.querySelector('.lg').value='';"
        "d.querySelector('.lp').value='';renderTiers(d);recalc();});"
        "d.querySelector('.lp').addEventListener('input',recalc);"
        "d.querySelector('.rm').addEventListener('click',function(){"
        "if(linesEl.children.length>1){d.remove();recalc();}else{alert('Mindestens eine Zeile.');}});"
        "linesEl.appendChild(d);renderTiers(d);}"
        "document.getElementById('addline').addEventListener('click',makeLine);"
        "makeLine();"
        "document.getElementById('saleform').addEventListener('submit',function(e){"
        "var ok=false;linesEl.querySelectorAll('.line').forEach(function(l){"
        "if(parseFloat(l.querySelector('.lg').value)>0&&parseFloat(l.querySelector('.lp').value)>=0)ok=true;});"
        "if(!ok){e.preventDefault();alert('Bitte mindestens ein Produkt mit Menge und Preis eintragen.');}});"
        "</script>"
    )
    return shell("Lager & Umsatz", body)


# ------------------------------------------------------------------ Server
def token_for(pw):
    return hashlib.sha256(("saltyshop::" + pw).encode()).hexdigest()


VALID_TOKEN = token_for(ADMIN_PW)


class Handler(BaseHTTPRequestHandler):
    server_version = "AdminTool/1.0"

    def log_message(self, *a):
        pass

    # -- helpers
    def authed(self):
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "auth" in cookie and cookie["auth"].value == VALID_TOKEN

    def send_html(self, body, code=200, extra_headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, extra_headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def read_form_multi(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        multi = parse_qs(raw, keep_blank_values=True)
        single = {k: v[0] for k, v in multi.items()}
        return single, multi

    # -- routing
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self.send_html("ok")
            return
        if not self.authed():
            self.send_html(login_page())
            return
        if path == "/":
            flash = ""
            q = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            if q.get("ok"):
                flash = {"sale": "Verkauf gebucht ✓", "restock": "Lager aktualisiert ✓",
                         "del": "Verkauf gelöscht ✓"}.get(q["ok"][0], "")
            self.send_html(dashboard(flash))
        else:
            self.redirect("/")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/login":
            form = self.read_form()
            if form.get("pw", "") == ADMIN_PW:
                c = http.cookies.SimpleCookie()
                c["auth"] = VALID_TOKEN
                c["auth"]["path"] = "/"
                c["auth"]["max-age"] = 60 * 60 * 24 * 30
                c["auth"]["samesite"] = "Lax"
                self.redirect("/", [("Set-Cookie", c["auth"].OutputString())])
            else:
                self.send_html(login_page("Falsches Passwort."))
            return

        if not self.authed():
            self.send_html(login_page())
            return

        form, multi = self.read_form_multi()
        try:
            if path == "/sale":
                cust = (form.get("customer") or "").strip()[:60]
                sdate = form.get("sale_date") or today_str()
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", sdate):
                    sdate = today_str()
                prods = multi.get("line_product", [])
                gramss = multi.get("line_grams", [])
                prices = multi.get("line_price", [])
                booked = 0
                for i in range(min(len(prods), len(gramss), len(prices))):
                    pid = prods[i]
                    try:
                        grams = float((gramss[i] or "0").replace(",", "."))
                        price = int(float((prices[i] or "0").replace(",", ".")))
                    except (ValueError, TypeError):
                        continue
                    if pid in PROD_BY_ID and grams > 0 and price >= 0:
                        add_sale(cust, pid, grams, price, sdate)
                        booked += 1
                if booked:
                    self.redirect("/?ok=sale")
                    return
            elif path == "/restock":
                pid = form.get("product_id", "")
                grams = float((form.get("grams") or "0").replace(",", "."))
                mode = "set" if form.get("mode") == "set" else "add"
                if pid in PROD_BY_ID:
                    restock(pid, grams, mode)
                    self.redirect("/?ok=restock")
                    return
            elif path == "/delete":
                sid = int(form.get("id", "0"))
                delete_sale(sid)
                self.redirect("/?ok=del")
                return
        except (ValueError, TypeError):
            pass
        self.redirect("/")


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Lager & Umsatz laeuft auf http://0.0.0.0:{PORT}  (Passwort: {'gesetzt' if ADMIN_PW!='changeme' else 'changeme — bitte aendern!'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
