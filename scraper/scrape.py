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
    h = re.sub(r"<img\b[^>]*>", "", h)
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


FUENTES = {"TheSportsDB": fuente_thesportsdb, "ESPN": fuente_espn, "Sofascore": fuente_sofascore}


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
