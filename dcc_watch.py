#!/usr/bin/env python3
"""
Hlídač nabídky diecastcompany.nl
--------------------------------
Pro vybrané výrobce (Mini GT, Tarmac, ...) stáhne kompletní seznam položek,
porovná ho s minulým během a nahlásí, co přibylo, co zmizelo a u čeho se
změnil stav (pre-order -> skladem apod.). Umí poslat report na Telegram
a/nebo e-mailem.

Použití:
    python dcc_watch.py --list           # vypíše všechny výrobce z webu
    python dcc_watch.py                  # normální běh (hlídá výrobce z config.json)
    python dcc_watch.py --dry-run        # spočítá rozdíly, ale nic neposílá
    python dcc_watch.py --only "Mini GT" # jen jeden výrobce
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.diecastcompany.nl/"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
REPORT_DIR = HERE / "reports"
DOCS_DIR = HERE / "docs"          # data pro webovou aplikaci (GitHub Pages)
HISTORY_PATH = DOCS_DIR / "history.json"
DATA_PATH = DOCS_DIR / "data.json"
HISTORY_LIMIT = 400

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en,cs;q=0.8",
}

ARTICLE_RE = re.compile(r"/article/([^/?#]+)")
PAGE_RE = re.compile(r"pagina=(\d+)")
HITS_RE = re.compile(r"(\d+)\s+hits", re.I)
AVAIL_RE = re.compile(r"Available\s*:\s*([A-Za-z]+\s+\d{4})")


# ----------------------------------------------------------------- utils --
def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session, url, delay):
    """Stáhne stránku, při chybě zkusí ještě dvakrát."""
    for attempt in range(3):
        try:
            r = session.get(url, timeout=40)
            r.raise_for_status()
            time.sleep(delay)
            return r.text
        except requests.RequestException as e:
            log(f"  ! chyba {e} (pokus {attempt + 1}/3)")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Nepodařilo se stáhnout {url}")


# ---------------------------------------------------------- manufacturers --
def list_manufacturers(session, delay):
    """Vrátí {název: url} pro všechny výrobce, které web nabízí."""
    soup = BeautifulSoup(fetch(session, BASE, delay), "html.parser")
    found = {}

    # 1) loga výrobců na homepage
    for a in soup.find_all("a", href=re.compile(r"/manufacturer-details/")):
        img = a.find("img")
        name = (img.get("alt") if img else None) or a.get_text(strip=True)
        name = name.split("|")[0].strip()
        if name:
            found[name] = urljoin(BASE, a["href"])

    # 2) rozbalovací seznam "Manufacturer :" (obsahuje i výrobce bez loga,
    #    např. Tomica, Herpa, Vallejo ...)
    for sel in soup.find_all("select"):
        opts = sel.find_all("option")
        texts = [o.get_text(strip=True) for o in opts]
        if not any(t.lower().startswith("mini gt") for t in texts):
            continue  # to není seznam výrobců
        for o in opts:
            m = re.match(r"^(.+?)\s*\((\d+)\)\s*$", o.get_text(strip=True))
            if m:
                name = m.group(1).strip()
                found.setdefault(name, urljoin(BASE, "manufacturer-details/" + quote(name.lower())))
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


# ------------------------------------------------------------- scraping --
IMG_ATTRS = ("data-src", "data-original", "data-lazy", "data-lazy-src", "data-srcset", "srcset", "src")
PLACEHOLDER_RE = re.compile(r"(blank|spacer|lazy|loading|pixel|placeholder|1x1)", re.I)


def image_from(img):
    """Vrátí URL obrázku z <img>, včetně lazy-load variant; placeholdery přeskočí."""
    if img is None:
        return ""
    candidates = []
    for attr in IMG_ATTRS:
        v = img.get(attr)
        if v:
            candidates.append(v.split(",")[0].split()[0])
    style = img.get("style") or ""
    m = re.search(r"url\(['\"]?([^'\")]+)", style)
    if m:
        candidates.append(m.group(1))
    for c in candidates:
        if not PLACEHOLDER_RE.search(c) and not c.startswith("data:"):
            return urljoin(BASE, c)
    return ""


def parse_articles(soup, manufacturer):
    """Vytáhne položky daného výrobce ze stránky s výsledky.

    Na stránce je i postranní sloupec "New Pre-Orders" s náhodnými položkami
    jiných výrobců, proto filtrujeme podle ' - <výrobce> - ' v názvu.
    """
    items = {}
    mkey = f" - {manufacturer.lower()} - "
    for a in soup.find_all("a", href=ARTICLE_RE):
        m = ARTICLE_RE.search(a["href"])
        code = m.group(1)
        img = a.find("img")
        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = html.unescape(title or "").split("| The Diecast Company")[0].strip(" |")
        rec = items.setdefault(code, {"code": code, "title": "", "status": "current",
                                      "available": "", "url": urljoin(BASE, a["href"]), "image": ""})
        if len(title) > len(rec["title"]):
            rec["title"] = title
        if not rec["image"]:
            rec["image"] = image_from(img)

        # stav zjistíme z bloku kolem odkazu (pre-order obrázek / "Available : ...")
        block = a
        for _ in range(6):
            if block.parent is None:
                break
            block = block.parent
            codes_in_block = {ARTICLE_RE.search(x["href"]).group(1)
                              for x in block.find_all("a", href=ARTICLE_RE)}
            if len(codes_in_block) > 1:
                break
            raw = str(block)
            text = block.get_text(" ", strip=True)
            if "titlePreorder" in raw or "Available :" in text:
                rec["status"] = "pre-order"
                am = AVAIL_RE.search(text)
                if am:
                    rec["available"] = am.group(1)
            if "titleSellout" in raw or "Special offer" in text:
                rec["status"] = "special-offer"
            if "sold out" in text.lower():
                rec["status"] = "sold-out"

    # jen položky tohoto výrobce (vyhodí sidebar)
    return {c: r for c, r in items.items() if mkey in f" {r['title'].lower()} "}


def scrape_manufacturer(session, name, url, delay):
    """Projde všechny stránky výrobce, vrátí (položky, počet_hits_na_webu)."""
    first = fetch(session, url, delay)
    soup = BeautifulSoup(first, "html.parser")
    hits = int(HITS_RE.search(soup.get_text(" ")).group(1)) if HITS_RE.search(soup.get_text(" ")) else None
    items = parse_articles(soup, name)

    # stránkování: odkazy obsahují pagina=N (+ nobotcode, který si web sám generuje)
    page_links = [a["href"] for a in soup.find_all("a", href=PAGE_RE)]
    last_page = max((int(PAGE_RE.search(h).group(1)) for h in page_links), default=1)
    template = None
    if page_links:
        template = urljoin(BASE, page_links[0])

    log(f"  {name}: {hits if hits is not None else '?'} hits, {last_page} stránek")
    for p in range(2, last_page + 1):
        purl = PAGE_RE.sub(f"pagina={p}", template)
        psoup = BeautifulSoup(fetch(session, purl, delay), "html.parser")
        got = parse_articles(psoup, name)
        if not got:
            log(f"  ! stránka {p} nevrátila žádné položky – možná vypršel nobotcode, načítám znovu")
            # obnovíme šablonu z první stránky a zkusíme ještě jednou
            soup = BeautifulSoup(fetch(session, url, delay), "html.parser")
            page_links = [a["href"] for a in soup.find_all("a", href=PAGE_RE)]
            if page_links:
                template = urljoin(BASE, page_links[0])
                purl = PAGE_RE.sub(f"pagina={p}", template)
                got = parse_articles(BeautifulSoup(fetch(session, purl, delay), "html.parser"), name)
        items.update(got)

    with_img = [r for r in items.values() if r.get("image")]
    log(f"  {name}: {len(with_img)}/{len(items)} položek má obrázek"
        + (f", např. {with_img[0]['image']}" if with_img else ""))
    if hits and len(items) < hits * 0.8:
        log(f"  ! varování: načteno {len(items)} položek, web hlásí {hits} – parser možná něco přehlíží")
    return items, hits


# ----------------------------------------------------------------- diff --
def diff_manufacturer(name, old, new, now):
    """Vrátí dict s klíči added / removed / changed / baseline."""
    result = {"added": [], "removed": [], "changed": [], "baseline": False, "count": len(new)}
    if not old:
        result["baseline"] = True
        return result
    for code, rec in new.items():
        prev = old.get(code)
        if prev is None or not prev.get("active", True):
            result["added"].append(rec)
        elif prev.get("status") != rec["status"] or prev.get("available") != rec["available"]:
            result["changed"].append({"old": prev, "new": rec})
    for code, prev in old.items():
        if prev.get("active", True) and code not in new:
            result["removed"].append(prev)
    return result


def merge_state(old, new, now):
    merged = {}
    for code, prev in old.items():
        merged[code] = dict(prev)
        if code not in new:
            merged[code]["active"] = False
            merged[code].setdefault("removed_at", now)
    for code, rec in new.items():
        prev = old.get(code, {})
        merged[code] = {**rec, "first_seen": prev.get("first_seen", now),
                        "last_seen": now, "active": True}
        merged[code].pop("removed_at", None)
    return merged


# ------------------------------------------------------- web app data --
def write_site_data(state, results, now_s):
    """Zapíše docs/data.json (aktuální nabídka) a docs/history.json (změny)."""
    DOCS_DIR.mkdir(exist_ok=True)
    data = {"generated": now_s, "manufacturers": {}}
    for name, items in state.items():
        active = [{k: v for k, v in r.items() if k in ("code", "title", "status", "available", "url", "first_seen", "image")}
                  for r in items.values() if r.get("active", True)]
        active.sort(key=lambda r: (r.get("first_seen", ""), r["code"]), reverse=True)
        data["manufacturers"][name] = {"count": len(active), "items": active}
    save_json(DATA_PATH, data)

    history = load_json(HISTORY_PATH, [])
    for name, d in results.items():
        if d["baseline"] or not (d["added"] or d["removed"] or d["changed"]):
            continue
        history.insert(0, {"time": now_s, "manufacturer": name,
                           "added": d["added"], "removed": d["removed"], "changed": d["changed"]})
    save_json(HISTORY_PATH, history[:HISTORY_LIMIT])


# --------------------------------------------------------------- report --
def fmt_item(r):
    extra = ""
    if r.get("status") == "pre-order":
        extra = f" [pre-order{' ' + r['available'] if r.get('available') else ''}]"
    elif r.get("status") not in ("current", None):
        extra = f" [{r['status']}]"
    return f"• {r['title']}{extra}\n  {r['url']}"


def build_report(results, now):
    lines = [f"Diecastcompany.nl – změny k {now:%d.%m.%Y %H:%M}", ""]
    anything = False
    for name, d in results.items():
        if d["baseline"]:
            lines.append(f"▪ {name}: první načtení, uloženo {d['count']} položek (příště už hlásím rozdíly)")
            lines.append("")
            continue
        if not (d["added"] or d["removed"] or d["changed"]):
            continue
        anything = True
        lines.append(f"▪ {name}  (+{len(d['added'])} / −{len(d['removed'])} / ~{len(d['changed'])}, celkem {d['count']})")
        if d["added"]:
            lines.append("  PŘIDÁNO:")
            lines += ["  " + fmt_item(r).replace("\n", "\n  ") for r in d["added"]]
        if d["removed"]:
            lines.append("  ZMIZELO:")
            lines += ["  " + fmt_item(r).replace("\n", "\n  ") for r in d["removed"]]
        if d["changed"]:
            lines.append("  ZMĚNA STAVU:")
            for ch in d["changed"]:
                o, n = ch["old"], ch["new"]
                lines.append(f"  • {n['title']}: {o.get('status')} {o.get('available', '')} → "
                             f"{n['status']} {n.get('available', '')}\n    {n['url']}")
        lines.append("")
    return anything, "\n".join(lines).rstrip()


# --------------------------------------------------------------- notify --
def send_telegram(cfg, text):
    token = os.environ.get("TELEGRAM_TOKEN") or cfg.get("token")
    chat_ids = cfg.get("chat_ids") or []
    if not token or not chat_ids:
        log("  Telegram: chybí token nebo chat_ids, přeskakuji")
        return
    # Telegram má limit 4096 znaků na zprávu
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 3900:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    chunks.append(buf)
    for cid in chat_ids:
        for chunk in chunks:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": cid, "text": chunk, "disable_web_page_preview": True},
                              timeout=30)
            if not r.ok:
                log(f"  Telegram: chyba {r.status_code} {r.text[:200]}")
    log("  Telegram: odesláno")


def send_email(cfg, text, now):
    password = os.environ.get("SMTP_PASSWORD") or cfg.get("password")
    if not (cfg.get("host") and cfg.get("user") and password and cfg.get("to")):
        log("  E-mail: chybí nastavení, přeskakuji")
        return
    msg = EmailMessage()
    msg["Subject"] = f"Diecastcompany.nl – nové položky {now:%d.%m.%Y}"
    msg["From"] = cfg.get("from") or cfg["user"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(text)
    port = int(cfg.get("port", 465))
    if cfg.get("starttls"):
        with smtplib.SMTP(cfg["host"], port, timeout=30) as s:
            s.starttls()
            s.login(cfg["user"], password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(cfg["host"], port, timeout=30) as s:
            s.login(cfg["user"], password)
            s.send_message(msg)
    log("  E-mail: odesláno")


# ---------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="vypíše výrobce z webu a skončí")
    ap.add_argument("--dry-run", action="store_true", help="neposílá notifikace, jen vypíše report")
    ap.add_argument("--only", help="zpracuje jen jednoho výrobce (název jako v config.json)")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    delay = float(cfg.get("delay_seconds", 1.5))
    session = make_session()

    if args.list:
        for name, url in list_manufacturers(session, delay).items():
            print(f"{name}\t{url}")
        return

    watched = cfg.get("manufacturers") or []
    if args.only:
        watched = [args.only]
    if not watched:
        sys.exit("V config.json není žádný výrobce v 'manufacturers'. Spusť --list a nějaké vyber.")

    now = datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M")
    state = load_json(STATE_PATH, {})
    results = {}

    for name in watched:
        log(f"Načítám {name} ...")
        url = urljoin(BASE, "manufacturer-details/" + quote(name.lower()))
        try:
            items, hits = scrape_manufacturer(session, name, url, delay)
        except Exception as e:
            log(f"  ! {name}: selhalo ({e}), přeskakuji – stav nechávám beze změny")
            continue
        if hits and not items:
            log(f"  ! {name}: web hlásí {hits} položek, ale nic jsem nenačetl – neaktualizuji stav")
            continue
        old = state.get(name, {})
        results[name] = diff_manufacturer(name, old, items, now_s)
        state[name] = merge_state(old, items, now_s)
        log(f"  {name}: +{len(results[name]['added'])} −{len(results[name]['removed'])} "
            f"~{len(results[name]['changed'])}")

    save_json(STATE_PATH, state)
    write_site_data(state, results, now_s)
    anything, report = build_report(results, now)
    print("\n" + report + "\n")

    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"{now:%Y-%m-%d_%H%M}.txt").write_text(report, encoding="utf-8")

    baseline_only = all(d["baseline"] for d in results.values()) if results else False
    if args.dry_run:
        log("Dry run – nic neposílám.")
        return
    if not anything and not (baseline_only and cfg.get("notify_on_baseline", True)):
        log("Žádné změny, notifikaci neposílám.")
        return
    if cfg.get("telegram", {}).get("enabled"):
        send_telegram(cfg["telegram"], report)
    if cfg.get("email", {}).get("enabled"):
        send_email(cfg["email"], report, now)


if __name__ == "__main__":
    main()
