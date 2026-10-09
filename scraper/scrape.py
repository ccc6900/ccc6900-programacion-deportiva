#!/usr/bin/env python3
"""Recoge la programación deportiva de los próximos días y la guarda en data/AAAA-MM-DD.json.
Se ejecuta en GitHub Actions. Solo usa la librería estándar.
Cada fuente es independiente: si una falla, las demás siguen."""
import json, os, re, sys, unicodedata, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAS = 7
TSDB_KEY = os.environ.get("THESPORTSDB_KEY") or "3"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}


def http(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def get_json(url):
    return json.loads(http(url).decode("utf-8"))


def norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b(fc|cf|cd|ud|sd|rc|ca|real|club|the|de|la|el)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


# ------------------------------------------------------------ fuentes con API
def fuente_thesportsdb(fecha):
    j = get_json(f"https://www.thesportsdb.com/api/v1/json/{TSDB_KEY}/eventstv.php?d={fecha}")
    out = {}
    for x in j.get("tvevents") or []:
        k = x.get("idEvent") or x.get("strEvent")
        e = out.setdefault(k, {
            "nombre": x.get("strEvent"), "deporte": x.get("strSport") or "Otros",
            "liga": x.get("strLeague") or "", "ts": x.get("strTimestamp") or x.get("strTimeStamp"),
            "equipos": [x.get("strHomeTeam"), x.get("strAwayTeam")], "canales": []})
        c = x.get("strChannel") or x.get("strTVStation")
        if c:
            e["canales"].append({"canal": c, "pais": x.get("strCountry") or "Internacional"})
    return list(out.values())


ESPN = [("soccer", "esp.1"), ("soccer", "esp.2"), ("soccer", "eng.1"), ("soccer", "ita.1"),
        ("soccer", "ger.1"), ("soccer", "fra.1"), ("soccer", "por.1"), ("soccer", "ned.1"),
        ("soccer", "uefa.champions"), ("soccer", "uefa.europa"), ("soccer", "uefa.europa.conf"),
        ("soccer", "usa.1"), ("soccer", "mex.1"), ("soccer", "arg.1"), ("soccer", "bra.1"),
        ("basketball", "nba"), ("basketball", "wnba"), ("football", "nfl"), ("baseball", "mlb"),
        ("hockey", "nhl"), ("racing", "f1"), ("mma", "ufc")]


def _espn(par, fecha):
    dep, liga = par
    try:
        j = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{dep}/{liga}/scoreboard?dates={fecha.replace('-', '')}")
    except Exception:
        return []
    nl = (j.get("leagues") or [{}])[0].get("name", liga)
    res = []
    for ev in j.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        canales = []
        for g in comp.get("geoBroadcasts") or []:
            n = (g.get("media") or {}).get("shortName")
            if n:
                canales.append({"canal": n, "pais": (g.get("region") or "us").upper()})
        for b in comp.get("broadcasts") or []:
            canales += [{"canal": n, "pais": "US"} for n in b.get("names") or []]
        eq = [(c.get("team") or {}).get("displayName") for c in comp.get("competitors") or []]
        res.append({"nombre": ev.get("name"), "deporte": dep.capitalize(), "liga": nl,
                    "ts": ev.get("date"), "equipos": eq, "canales": canales})
    return res


def fuente_espn(fecha):
    with ThreadPoolExecutor(8) as ex:
        return [e for l in ex.map(lambda p: _espn(p, fecha), ESPN) for e in l]


def fuente_sofascore(fecha):
    base = "https://api.sofascore.com/api/v1"
    evs = (get_json(f"{base}/sport/football/scheduled-events/{fecha}").get("events") or [])[:120]

    def uno(ev):
        canales = []
        try:
            t = get_json(f"{base}/tv/event/{ev['id']}/country-channels")
            nombres = {str(c.get("id")): c.get("name") for c in t.get("channels", [])} \
                if isinstance(t.get("channels"), list) else {}
            for pais, ids in (t.get("countryChannels") or {}).items():
                for i in ids:
                    n = i.get("name") if isinstance(i, dict) else nombres.get(str(i))
                    if n:
                        canales.append({"canal": n, "pais": pais})
        except Exception:
            pass
        h, a = ev["homeTeam"]["name"], ev["awayTeam"]["name"]
        return {"nombre": f"{h} vs {a}", "deporte": "Football", "equipos": [h, a],
                "ts": datetime.fromtimestamp(ev["startTimestamp"], timezone.utc).isoformat(),
                "liga": (ev.get("tournament") or {}).get("name", ""), "canales": canales}

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(uno, evs))


# ------------------------------------------------------------ webs (pendientes de parser)
# Cuando tengamos el HTML real de cada web (carpeta debug/), se añaden aquí funciones
# fuente_whatsportson(fecha), fuente_sportzap(fecha)... y se registran en FUENTES.
WEBS = {
    "whatsportson": "https://www.whatsportson.com/",
    "livesportsin": "https://livesportsin.com/watch",
    "sportzap": "https://sportzap.tv/",
    "worldtvradio": "https://www.worldtvradio.com/",
    "calaverasports": "https://calaverasports.com/",
    "livesoccertv": "https://www.livesoccertv.com/es/",
    "relevo": "https://www.relevo.com/agenda-deportiva/",
    "sincroguia": "https://sincroguia-tv.expansion.com/programacion-tv/retransmisiones-deportivas",
    "watchsportsguide": "https://watchsportsguide.com/",
}


def limpiar_html(h):
    """Quita menús, scripts, estilos e iconos para dejar solo el contenido útil."""
    for tag in ("script", "style", "svg", "nav", "noscript", "header", "footer", "head"):
        h = re.sub(rf"<{tag}\b.*?</{tag}>", "", h, flags=re.S | re.I)
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    h = re.sub(r"<img\b[^>]*?\balt=\"([^\"]*)\"[^>]*>", r'<img alt="\1">', h)
    h = re.sub(r"<img\b(?![^>]*alt=)[^>]*>", "", h)
    h = re.sub(r"\s(?:style|data-[a-z-]+|aria-[a-z-]+|d|viewBox|fill|stroke[a-z-]*)=\"[^\"]*\"", "", h)
    return re.sub(r"\s+", " ", h)


def guardar_html_debug():
    """Guarda el HTML de cada web (bruto y limpio) para poder escribir sus parsers."""
    for nombre, url in WEBS.items():
        d = ROOT / "debug"
        try:
            raw = http(url, 25).decode("utf-8", "replace")
            (d / f"{nombre}.html").write_text(raw[:1_500_000], encoding="utf-8")
            (d / f"{nombre}.limpio.html").write_text(limpiar_html(raw)[:150_000], encoding="utf-8")
        except Exception as e:
            (d / f"{nombre}.html").write_text(f"ERROR: {e}", encoding="utf-8")


# ------------------------------------------------------------ WhatSportsOn
import html as _html
from urllib.parse import urlparse
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

EMOJI_DEPORTE = {"⚽": "Football", "🏀": "Basketball", "⚾": "Baseball", "🏒": "Ice Hockey", "⛳": "Golf",
                 "🎾": "Tennis", "🏏": "Cricket", "🏈": "American Football", "🏃": "Athletics",
                 "🏉": "Rugby / AFL", "🥊": "Boxing", "🚴": "Cycling", "🎯": "Darts", "🐴": "Equestrian",
                 "🏎": "Motorsport", "🏅": "Motorsport", "🐎": "Horse Racing", "🏊": "Swimming",
                 "🏐": "Volleyball", "🥋": "Fighting", "🏌": "Golf"}
TLD_PAIS = {"ca": "CA", "pl": "PL", "au": "AU", "uk": "GB", "es": "ES", "mx": "MX", "de": "DE", "fr": "FR",
            "it": "IT", "br": "BR", "ar": "AR", "jp": "JP", "nl": "NL", "pt": "PT", "tr": "TR", "ie": "IE",
            "nz": "NZ", "za": "ZA", "se": "SE", "no": "NO", "dk": "DK", "be": "BE", "ch": "CH", "at": "AT"}
_WSO_CACHE = {}


def _txt(h):
    return _html.unescape(re.sub(r"<[^>]+>", "", h or "")).strip()


def parsear_whatsportson(h):
    eventos = []
    for art in re.findall(r"<article\b.*?</article>", h, flags=re.S):
        m = re.search(r'href="/events/(\d+)/[^"]*?(\d{4}-\d{2}-\d{2})"', art)
        t = re.search(r"<time[^>]*>(\d{1,2}:\d{2})</time>", art)
        ti = re.search(r'<p class="mt-1 [^"]*">(.*?)</p>', art, flags=re.S)
        if not (m and t and ti):
            continue
        emo = re.search(r'<span class="text-sm leading-none">([^<]+)</span>', art)
        comp = re.search(r'<a href="/competition/[^"]*"><span[^>]*>(.*?)</span></a>', art, flags=re.S)
        sub = re.search(r'<p class="mt-0\.5 text-xs[^"]*">(.*?)</p>', art, flags=re.S)
        canales = []
        for href, inner in re.findall(r'<a href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>(.*?)</a>', art, flags=re.S):
            alt = re.search(r'alt="([^"]*)"', inner)
            nombre = _html.unescape(alt.group(1)).strip() if alt and alt.group(1).strip() else ""
            if not nombre:
                tx = _txt(re.sub(r"FREE", "", inner)).strip()
                nombre = tx
            host = urlparse(href).netloc.replace("www.", "")
            if not nombre:
                nombre = host if "whatsportson.com" not in host else ""
            if not nombre:
                continue
            pais = TLD_PAIS.get(host.rsplit(".", 1)[-1], "Internacional")
            canales.append({"canal": nombre, "pais": pais})
        sin = re.search(r"Channel TBC", art)
        titulo = _txt(ti.group(1))
        equipos = [x.strip() for x in re.split(r"\s+v\s+", titulo)] if " v " in titulo else []
        emoji = (emo.group(1).strip() if emo else "").replace("\ufe0f", "")
        fecha, hora = m.group(2), t.group(1)
        ts = None
        try:
            tz = ZoneInfo("Europe/London") if ZoneInfo else timezone.utc
            dt = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            ts = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
        eventos.append({"fecha": fecha, "nombre": titulo, "deporte": EMOJI_DEPORTE.get(emoji, "Otros"),
                        "liga": _txt(comp.group(1)) if comp else "", "ts": ts,
                        "equipos": equipos if len(equipos) == 2 else [], "canales": canales,
                        "detalle": _txt(sub.group(1)) if sub else "", "id": m.group(1)})
    # sin duplicados por id
    vistos, out = set(), []
    for e in eventos:
        if e["id"] not in vistos:
            vistos.add(e["id"])
            out.append(e)
    return out


def fuente_whatsportson(fecha):
    if "ev" not in _WSO_CACHE:
        _WSO_CACHE["ev"] = parsear_whatsportson(http("https://www.whatsportson.com/", 30).decode("utf-8", "replace"))
    res = []
    for e in _WSO_CACHE["ev"]:
        if e["fecha"] == fecha:
            e = {k: v for k, v in e.items() if k not in ("fecha", "id", "detalle")}
            res.append(e)
    return res


# ------------------------------------------------------------ Relevo (España)
SPORT_RELEVO = {"sport-futbol": "Football", "sport-tenis": "Tennis", "sport-baloncesto": "Basketball",
                "sport-formula-1": "Motorsport", "sport-motociclismo": "Motorsport",
                "sport-ciclismo": "Cycling", "sport-otros": "Otros", "sport-otros-deportes": "Otros"}
_RELEVO_CACHE = {}


def parsear_relevo(h):
    eventos = []
    for clase, bloque in re.findall(r'<div class="sports-agenda-group (sport-[\w-]+)">(.*?)</ul>', h, flags=re.S):
        n = re.search(r'sports-agenda-group-name">(.*?)</span>', bloque, flags=re.S)
        liga = _txt(n.group(1)) if n else ""
        for li in re.findall(r'<li class="sports-agenda-event">(.*?)</li>', bloque, flags=re.S):
            t = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', li)
            tv = re.search(r'<span class="sports-agenda-tv">(.*?)</span>', li, flags=re.S)
            resto = re.sub(r'<span class="sports-agenda-tv">.*?</span>', "", li, flags=re.S)
            resto = re.sub(r'<a class="sports-agenda-pick".*?</a>', "", resto, flags=re.S)
            resto = re.sub(r"<time.*?</time>", "", resto, flags=re.S)
            resto = re.sub(r'<span class="sports-agenda-time[^"]*">.*?</span>', "", resto, flags=re.S)
            texto = re.sub(r"\s+", " ", _txt(resto))
            if not texto or not t:
                continue
            partes = [x.strip() for x in re.split(r"\s[–-]\s", texto)]
            equipos = partes if len(partes) == 2 else []
            nombre = f"{partes[0]} vs {partes[1]}" if equipos else f"{liga}: {texto}"
            tz = ZoneInfo("Europe/Madrid") if ZoneInfo else timezone.utc
            ts = datetime.strptime(f"{t.group(1)} {t.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=tz) \
                .astimezone(timezone.utc).isoformat()
            canal = _txt(tv.group(1)) if tv else ""
            eventos.append({"fecha": t.group(1), "nombre": nombre, "deporte": SPORT_RELEVO.get(clase, "Otros"),
                            "liga": liga, "ts": ts, "equipos": equipos,
                            "canales": [{"canal": canal, "pais": "ES"}] if canal else []})
    return eventos


def fuente_relevo(fecha):
    if "ev" not in _RELEVO_CACHE:
        _RELEVO_CACHE["ev"] = parsear_relevo(
            http("https://www.relevo.com/agenda-deportiva/", 30).decode("utf-8", "replace"))
    return [{k: v for k, v in e.items() if k != "fecha"} for e in _RELEVO_CACHE["ev"] if e["fecha"] == fecha]


FUENTES = {"TheSportsDB": fuente_thesportsdb, "ESPN": fuente_espn, "Sofascore": fuente_sofascore,
           "WhatSportsOn": fuente_whatsportson, "Relevo": fuente_relevo}


# ------------------------------------------------------------ unión
def clave(e):
    eq = sorted(norm(x) for x in (e.get("equipos") or []) if x)
    return "|".join(eq) if len(eq) == 2 else norm(e.get("nombre"))


def dia(fecha):
    unidos, estado = {}, {}
    with ThreadPoolExecutor(len(FUENTES)) as ex:
        futs = {n: ex.submit(f, fecha) for n, f in FUENTES.items()}
    for n, fut in futs.items():
        try:
            lista = fut.result()
            estado[n] = f"{len(lista)} eventos"
        except Exception as err:
            estado[n] = f"error: {err}"
            continue
        for e in lista:
            u = unidos.setdefault(clave(e), {**e, "canales": [], "fuentes": []})
            if n not in u["fuentes"]:
                u["fuentes"].append(n)
            vistos = {(c["canal"].lower(), c["pais"].upper()) for c in u["canales"]}
            for c in e["canales"]:
                k = (c["canal"].lower(), c["pais"].upper())
                if k not in vistos:
                    u["canales"].append(c)
                    vistos.add(k)
            u["ts"] = u.get("ts") or e.get("ts")
    evs = sorted(unidos.values(), key=lambda e: e.get("ts") or "")
    for e in evs:
        e.pop("equipos", None)
    return {"fecha": fecha, "actualizado": datetime.now(timezone.utc).isoformat(), "estado": estado, "eventos": evs}


if __name__ == "__main__":
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "debug").mkdir(exist_ok=True)
    hoy = datetime.now(timezone.utc).date()
    fechas = [(hoy + timedelta(days=i)).isoformat() for i in range(-1, DIAS)]
    for f in fechas:
        d = dia(f)
        (ROOT / "data" / f"{f}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f, d["estado"], file=sys.stderr)
    (ROOT / "data" / "index.json").write_text(json.dumps({"fechas": fechas}), encoding="utf-8")
    for old in (ROOT / "data").glob("20*.json"):
        if old.stem not in fechas:
            old.unlink()
    guardar_html_debug()
