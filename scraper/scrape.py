
#!/usr/bin/env python3
"""Agenda deportiva y canales de TV para GitHub Actions."""
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

ROOT = Path(__file__).resolve().parent.parent
DIAS = 7
TSDB_KEY = os.environ.get("THESPORTSDB_KEY") or "3"

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

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


def http(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def get_json(url):
    return json.loads(http(url).decode("utf-8"))


def texto(fragmento):
    return html.unescape(
        re.sub(r"<[^>]+>", " ", fragmento or "")
    ).replace("\xa0", " ").strip()


def norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(
        r"\b(fc|cf|cd|ud|sd|rc|ca|real|club|the|de|la|el)\b",
        " ", s
    )
    return re.sub(r"[^a-z0-9]", "", s)


def guardar_html_debug():
    """Guarda HTML para desarrollar y revisar nuevos extractores."""
    carpeta = ROOT / "debug"
    carpeta.mkdir(parents=True, exist_ok=True)

    for nombre, url in WEBS.items():
        try:
            raw = http(url, 25).decode("utf-8", "replace")
            (carpeta / f"{nombre}.html").write_text(
                raw[:1_500_000], encoding="utf-8"
            )
            limpio = re.sub(
                r"<(script|style|noscript)\b.*?</\1>",
                " ", raw, flags=re.I | re.S
            )
            (carpeta / f"{nombre}.limpio.html").write_text(
                limpio[:150_000], encoding="utf-8"
            )
        except Exception as exc:
            (carpeta / f"{nombre}.html").write_text(
                f"ERROR: {exc}", encoding="utf-8"
            )


# ============================================================
# THESPORTSDB
# ============================================================

def fuente_thesportsdb(fecha):
    url = (
        "https://www.thesportsdb.com/api/v1/json/"
        f"{TSDB_KEY}/eventstv.php?d={fecha}"
    )
    j = get_json(url)
    eventos = {}

    for x in j.get("tvevents") or []:
        key = x.get("idEvent") or x.get("strEvent")
        e = eventos.setdefault(key, {
            "nombre": x.get("strEvent") or "Evento deportivo",
            "deporte": x.get("strSport") or "Otros",
            "liga": x.get("strLeague") or "",
            "ts": x.get("strTimestamp") or x.get("strTimeStamp"),
            "equipos": [x.get("strHomeTeam"), x.get("strAwayTeam")],
            "canales": [],
        })

        canal = x.get("strChannel") or x.get("strTVStation")
        if canal:
            e["canales"].append({
                "canal": canal,
                "pais": x.get("strCountry") or "Internacional",
            })

    return list(eventos.values())


# ============================================================
# ESPN
# ============================================================

ESPN = [
    ("soccer", "esp.1"),
    ("soccer", "esp.2"),
    ("soccer", "eng.1"),
    ("soccer", "ita.1"),
    ("soccer", "ger.1"),
    ("soccer", "fra.1"),
    ("soccer", "por.1"),
    ("soccer", "ned.1"),
    ("soccer", "uefa.champions"),
    ("soccer", "uefa.europa"),
    ("soccer", "uefa.europa.conf"),
    ("soccer", "usa.1"),
    ("soccer", "mex.1"),
    ("soccer", "arg.1"),
    ("soccer", "bra.1"),
    ("basketball", "nba"),
    ("basketball", "wnba"),
    ("football", "nfl"),
    ("baseball", "mlb"),
    ("hockey", "nhl"),
    ("racing", "f1"),
    ("mma", "ufc"),
]


def _espn(par, fecha):
    deporte, liga = par
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/"
        f"{deporte}/{liga}/scoreboard?dates={fecha.replace('-', '')}"
    )

    try:
        j = get_json(url)
    except Exception:
        return []

    nombre_liga = (j.get("leagues") or [{}])[0].get("name", liga)
    resultado = []

    for ev in j.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        canales = []

        for item in comp.get("geoBroadcasts") or []:
            nombre = (item.get("media") or {}).get("shortName")
            if nombre:
                canales.append({
                    "canal": nombre,
                    "pais": (item.get("region") or "US").upper(),
                })

        for item in comp.get("broadcasts") or []:
            for nombre in item.get("names") or []:
                canales.append({"canal": nombre, "pais": "US"})

        equipos = [
            (c.get("team") or {}).get("displayName")
            for c in comp.get("competitors") or []
        ]

        resultado.append({
            "nombre": ev.get("name") or "Evento deportivo",
            "deporte": deporte.capitalize(),
            "liga": nombre_liga,
            "ts": ev.get("date"),
            "equipos": equipos,
            "canales": canales,
        })

    return resultado


def fuente_espn(fecha):
    with ThreadPoolExecutor(max_workers=8) as pool:
        grupos = pool.map(lambda p: _espn(p, fecha), ESPN)
        return [e for grupo in grupos for e in grupo]


# ============================================================
# SOFASCORE
# ============================================================

def fuente_sofascore(fecha):
    base = "https://api.sofascore.com/api/v1"
    datos = get_json(
        f"{base}/sport/football/scheduled-events/{fecha}"
    )
    eventos = (datos.get("events") or [])[:120]

    def convertir(ev):
        canales = []

        try:
            tv = get_json(
                f"{base}/tv/event/{ev['id']}/country-channels"
            )

            nombres = {
                str(c.get("id")): c.get("name")
                for c in tv.get("channels", [])
                if isinstance(c, dict)
            }

            for pais, ids in (tv.get("countryChannels") or {}).items():
                for item in ids:
                    nombre = (
                        item.get("name")
                        if isinstance(item, dict)
                        else nombres.get(str(item))
                    )
                    if nombre:
                        canales.append({
                            "canal": nombre,
                            "pais": pais.upper(),
                        })
        except Exception:
            pass

        casa = (ev.get("homeTeam") or {}).get("name", "")
        fuera = (ev.get("awayTeam") or {}).get("name", "")

        return {
            "nombre": f"{casa} vs {fuera}",
            "deporte": "Football",
            "equipos": [casa, fuera],
            "ts": datetime.fromtimestamp(
                ev["startTimestamp"], timezone.utc
            ).isoformat(),
            "liga": (ev.get("tournament") or {}).get("name", ""),
            "canales": canales,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(convertir, eventos))


# ============================================================
# WHATSPORTSON
# ============================================================

EMOJI_DEPORTE = {
    "⚽": "Football",
    "🏀": "Basketball",
    "⚾": "Baseball",
    "🏒": "Ice Hockey",
    "⛳": "Golf",
    "🎾": "Tennis",
    "🏏": "Cricket",
    "🏈": "American Football",
    "🏃": "Athletics",
    "🏉": "Rugby / AFL",
    "🥊": "Boxing",
    "🚴": "Cycling",
    "🎯": "Darts",
    "🐴": "Equestrian",
    "🏎": "Motorsport",
    "🏅": "Motorsport",
    "🐎": "Horse Racing",
    "🏊": "Swimming",
    "🏐": "Volleyball",
    "🥋": "Fighting",
    "🏌": "Golf",
}

TLD_PAIS = {
    "ca": "CA", "pl": "PL", "au": "AU", "uk": "GB",
    "es": "ES", "mx": "MX", "de": "DE", "fr": "FR",
    "it": "IT", "br": "BR", "ar": "AR", "jp": "JP",
    "nl": "NL", "pt": "PT", "tr": "TR", "ie": "IE",
    "nz": "NZ", "za": "ZA", "se": "SE", "no": "NO",
    "dk": "DK", "be": "BE", "ch": "CH", "at": "AT",
}

_WSO_CACHE = None


def parsear_whatsportson(h):
    eventos = []

    for art in re.findall(
        r"<article\b.*?</article>", h, flags=re.S | re.I
    ):
        m = re.search(
            r'href="/events/(\d+)/[^"]*?(\d{4}-\d{2}-\d{2})',
            art
        )
        hora = re.search(
            r"<time[^>]*>(\d{1,2}:\d{2})</time>",
            art, flags=re.S
        )
        titulo = re.search(
            r'<p class="mt-1 [^"]*">(.*?)</p>',
            art, flags=re.S
        )

        if not (m and hora and titulo):
            continue

        emoji = re.search(
            r'<span class="text-sm leading-none">([^<]+)</span>',
            art
        )
        comp = re.search(
            r'<a href="/competition/[^"]*"><span[^>]*>(.*?)</span></a>',
            art, flags=re.S
        )

        canales = []
        enlaces = re.findall(
            r'<a href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>(.*?)</a>',
            art, flags=re.S | re.I
        )

        for href, interior in enlaces:
            alt = re.search(r'alt="([^"]*)"', interior)
            nombre = (
                html.unescape(alt.group(1)).strip()
                if alt and alt.group(1).strip()
                else texto(interior)
            )

            host = urlparse(href).netloc.replace("www.", "")
            nombre = re.sub(r"\bFREE\b", "", nombre, flags=re.I).strip()

            if not nombre and host and "whatsportson.com" not in host:
                nombre = host

            if nombre:
                extension = host.rsplit(".", 1)[-1]
                canales.append({
                    "canal": nombre,
                    "pais": TLD_PAIS.get(extension, "Internacional"),
                })

        nombre_evento = texto(titulo.group(1))
        equipos = (
            [p.strip() for p in re.split(r"\s+v\s+", nombre_evento)]
            if " v " in nombre_evento else []
        )

        fecha_evento = m.group(2)
        ts = None

        try:
            tz = (
                ZoneInfo("Europe/London")
                if ZoneInfo else timezone.utc
            )
            dt = datetime.strptime(
                f"{fecha_evento} {hora.group(1)}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)
            ts = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass

        icono = (
            emoji.group(1).strip().replace("\ufe0f", "")
            if emoji else ""
        )

        eventos.append({
            "fecha": fecha_evento,
            "id": m.group(1),
            "nombre": nombre_evento,
            "deporte": EMOJI_DEPORTE.get(icono, "Otros"),
            "liga": texto(comp.group(1)) if comp else "",
            "ts": ts,
            "equipos": equipos if len(equipos) == 2 else [],
            "canales": canales,
        })

    unicos = {}
    for evento in eventos:
        unicos.setdefault(evento["id"], evento)

    return list(unicos.values())


def fuente_whatsportson(fecha):
    global _WSO_CACHE

    if _WSO_CACHE is None:
        pagina = http(WEBS["whatsportson"], 30).decode(
            "utf-8", "replace"
        )
        _WSO_CACHE = parsear_whatsportson(pagina)

    return [
        {k: v for k, v in e.items() if k not in ("fecha", "id")}
        for e in _WSO_CACHE
        if e["fecha"] == fecha
    ]


# ============================================================
# RELEVO: AGENDA DEPORTIVA ESPAÑOLA
# ============================================================

SPORT_RELEVO = {
    "sport-futbol": "Football",
    "sport-tenis": "Tennis",
    "sport-baloncesto": "Basketball",
    "sport-formula-1": "Motorsport",
    "sport-motociclismo": "Motorsport",
    "sport-ciclismo": "Cycling",
    "sport-otros": "Otros",
    "sport-otros-deportes": "Otros",
}

_RELEVO_CACHE = None


def parsear_relevo(h):
    eventos = []

    grupos = re.findall(
        r'<div class="sports-agenda-group (sport-[\w-]+)">(.*?)</ul>',
        h, flags=re.S | re.I
    )

    for clase, bloque in grupos:
        mliga = re.search(
            r'sports-agenda-group-name">(.*?)</span>',
            bloque, flags=re.S
        )
        liga = texto(mliga.group(1)) if mliga else ""

        for li in re.findall(
            r'<li class="sports-agenda-event">(.*?)</li>',
            bloque, flags=re.S
        ):
            mt = re.search(
                r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})',
                li
            )
            if not mt:
                continue

            tv = re.search(
                r'<span class="sports-agenda-tv">(.*?)</span>',
                li, flags=re.S
            )

            resto = re.sub(
                r'<span class="sports-agenda-tv">.*?</span>',
                "", li, flags=re.S
            )
            resto = re.sub(
                r'<a class="sports-agenda-pick".*?</a>',
                "", resto, flags=re.S
            )
            resto = re.sub(r"<time.*?</time>", "", resto, flags=re.S)
            resto = re.sub(
                r'<span class="sports-agenda-time[^"]*">.*?</span>',
                "", resto, flags=re.S
            )

            nombre_bruto = re.sub(
                r"\s+", " ", texto(resto)
            ).strip()

            if not nombre_bruto:
                continue

            partes = [
                p.strip()
                for p in re.split(r"\s[–-]\s", nombre_bruto)
            ]
            equipos = partes if len(partes) == 2 else []

            nombre = (
                f"{partes[0]} vs {partes[1]}"
                if equipos
                else f"{liga}: {nombre_bruto}"
            )

            tz = (
                ZoneInfo("Europe/Madrid")
                if ZoneInfo else timezone.utc
            )
            dt = datetime.strptime(
                f"{mt.group(1)} {mt.group(2)}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=tz)

            canal = texto(tv.group(1)) if tv else ""

            eventos.append({
                "fecha": mt.group(1),
                "nombre": nombre,
                "deporte": SPORT_RELEVO.get(clase, "Otros"),
                "liga": liga,
                "ts": dt.astimezone(timezone.utc).isoformat(),
                "equipos": equipos,
                "canales": (
                    [{"canal": canal, "pais": "ES"}]
                    if canal else []
                ),
            })

    return eventos


def fuente_relevo(fecha):
    global _RELEVO_CACHE

    if _RELEVO_CACHE is None:
        pagina = http(WEBS["relevo"], 30).decode(
            "utf-8", "replace"
        )
        _RELEVO_CACHE = parsear_relevo(pagina)

    return [
        {k: v for k, v in e.items() if k != "fecha"}
        for e in _RELEVO_CACHE
        if e["fecha"] == fecha
    ]


# ============================================================
# SINCROGUIA: EXTRACTOR EXPERIMENTAL
# ============================================================

_SINCRO_CACHE = None


def parsear_sincroguia(h):
    """
    Intenta extraer tarjetas m-program.
    Si la web cambia su HTML, comprobar debug/sincroguia.html.
    """
    eventos = []

    # Cada tarjeta se delimita hasta el inicio de la siguiente.
    bloques = re.findall(
        r'<div\b[^>]*class="[^"]*\bm-program\b[^"]*"[^>]*>'
        r'.*?(?=<div\b[^>]*class="[^"]*\bm-program\b[^"]*"|\Z)',
        h, flags=re.S | re.I
    )

    for bloque in bloques:
        # Buscar un timestamp Unix de 10 dígitos en atributos.
        mt = re.search(r'(?<!\d)(\d{10})(?!\d)', bloque)
        if not mt:
            continue

        try:
            dt = datetime.fromtimestamp(
                int(mt.group(1)), timezone.utc
            )
        except (ValueError, OSError, OverflowError):
            continue

        mn = re.search(
            r'<h2\b[^>]*class="[^"]*\bm-program__name\b[^"]*"[^>]*>'
            r'(.*?)</h2>',
            bloque, flags=re.S | re.I
        )
        nombre = texto(mn.group(1)) if mn else ""
        if not nombre:
            continue

        mc = re.search(
            r'<a\b[^>]*class="[^"]*\bm-program__chanel\b[^"]*"'
            r'[^>]*>.*?<img\b[^>]*alt="([^"]+)"',
            bloque, flags=re.S | re.I
        )
        canal = html.unescape(mc.group(1)).strip() if mc else ""

        # Categoría del programa, si está disponible.
        mcat = re.search(
            r'class="[^"]*m-item-program__category-link[^"]*"'
            r'[^>]*>.*?<a\b[^>]*>(.*?)</a>',
            bloque, flags=re.S | re.I
        )
        categoria = texto(mcat.group(1)) if mcat else "Otros"
        cat = categoria.casefold()

        deporte = "Otros"
        if "fútbol" in cat or "futbol" in cat:
            deporte = "Football"
        elif "tenis" in cat:
            deporte = "Tennis"
        elif "baloncesto" in cat:
            deporte = "Basketball"
        elif "ciclismo" in cat:
            deporte = "Cycling"
        elif any(x in cat for x in ("motor", "fórmula", "formula", "moto")):
            deporte = "Motorsport"
        elif "rugby" in cat:
            deporte = "Rugby"
        elif "golf" in cat:
            deporte = "Golf"
        elif "padel" in cat or "pádel" in cat:
            deporte = "Padel"
        elif "hockey" in cat:
            deporte = "Hockey"

        eventos.append({
            "nombre": nombre,
            "deporte": deporte,
            "liga": categoria,
            "ts": dt.isoformat(),
            "equipos": [],
            "canales": (
                [{"canal": canal, "pais": "ES"}]
                if canal else []
            ),
            "_fecha_local": (
                dt.astimezone(ZoneInfo("Europe/Madrid")).date().isoformat()
                if ZoneInfo else dt.date().isoformat()
            ),
        })

    # Evitar tarjetas repetidas.
    unicos = {}
    for evento in eventos:
        clave = (
            evento["nombre"],
            evento["ts"],
            tuple(
                (c["canal"], c["pais"])
                for c in evento["canales"]
            ),
        )
        unicos.setdefault(clave, evento)

    return list(unicos.values())


def fuente_sincroguia(fecha):
    global _SINCRO_CACHE

    if _SINCRO_CACHE is None:
        pagina = http(WEBS["sincroguia"], 30).decode(
            "utf-8", "replace"
        )
        _SINCRO_CACHE = parsear_sincroguia(pagina)

    return [
        {k: v for k, v in e.items() if k != "_fecha_local"}
        for e in _SINCRO_CACHE
        if e.get("_fecha_local") == fecha
    ]


# ============================================================
# FUENTES ACTIVAS
# ============================================================

FUENTES = {
    "TheSportsDB": fuente_thesportsdb,
    "ESPN": fuente_espn,
    "Sofascore": fuente_sofascore,
    "WhatSportsOn": fuente_whatsportson,
    "Relevo": fuente_relevo,
    "SincroGuia": fuente_sincroguia,
}


# ============================================================
# UNIFICAR EVENTOS Y CANALES
# ============================================================

def clave(evento, fecha):
    equipos = sorted(
        norm(x) for x in (evento.get("equipos") or []) if x
    )

    if len(equipos) == 2:
        return f"{fecha}|{'|'.join(equipos)}"

    nombre = norm(evento.get("nombre"))
    ts = evento.get("ts") or ""
    return f"{fecha}|{nombre}|{ts[:13]}"


def dia(fecha):
    unidos = {}
    estado = {}

    with ThreadPoolExecutor(max_workers=len(FUENTES)) as pool:
        futuros = {
            nombre: pool.submit(funcion, fecha)
            for nombre, funcion in FUENTES.items()
        }

    for nombre_fuente, futuro in futuros.items():
        try:
            lista = futuro.result()
            estado[nombre_fuente] = f"{len(lista)} eventos"
        except Exception as exc:
            estado[nombre_fuente] = (
                f"error: {type(exc).__name__}: {exc}"
            )
            print(
                f"[AVISO] {nombre_fuente}, {fecha}: {exc}",
                file=sys.stderr
            )
            continue

        for evento in lista:
            k = clave(evento, fecha)

            if k not in unidos:
                unidos[k] = {
                    **evento,
                    "canales": [],
                    "fuentes": [],
                }

            unido = unidos[k]

            if nombre_fuente not in unido["fuentes"]:
                unido["fuentes"].append(nombre_fuente)

            vistos = {
                (
                    c.get("canal", "").strip().casefold(),
                    c.get("pais", "Internacional").upper(),
                )
                for c in unido["canales"]
            }

            for canal in evento.get("canales") or []:
                nombre_canal = (canal.get("canal") or "").strip()
                pais = (
                    canal.get("pais") or "Internacional"
                ).upper()

                clave_canal = (nombre_canal.casefold(), pais)

                if nombre_canal and clave_canal not in vistos:
                    unido["canales"].append({
                        "canal": nombre_canal,
                        "pais": pais,
                    })
                    vistos.add(clave_canal)

            # Completar datos vacíos con los de otras fuentes.
            for campo in ("ts", "liga", "deporte", "equipos"):
                if not unido.get(campo) and evento.get(campo):
                    unido[campo] = evento[campo]

    eventos = sorted(
        unidos.values(),
        key=lambda e: e.get("ts") or ""
    )

    for evento in eventos:
        evento.pop("equipos", None)
        evento.pop("_fecha_local", None)

    return {
        "fecha": fecha,
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "estado": estado,
        "eventos": eventos,
    }


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "debug").mkdir(exist_ok=True)

    hoy = datetime.now(timezone.utc).date()
    fechas = [
        (hoy + timedelta(days=i)).isoformat()
        for i in range(-1, DIAS)
    ]

    for fecha in fechas:
        resultado = dia(fecha)

        archivo = ROOT / "data" / f"{fecha}.json"
        archivo.write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print(fecha, resultado["estado"], file=sys.stderr)

    (ROOT / "data" / "index.json").write_text(
        json.dumps({"fechas": fechas}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Eliminar JSON antiguos que ya no estén en el índice.
    for antiguo in (ROOT / "data").glob("20*.json"):
        if antiguo.stem not in fechas:
            antiguo.unlink()

    # Guardar páginas para poder añadir más extractores.
    guardar_html_debug()
