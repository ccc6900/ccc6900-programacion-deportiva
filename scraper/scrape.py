
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recopila programación deportiva y canales.
Se ejecuta desde GitHub Actions y solo utiliza la biblioteca estándar.

Genera:
  data/AAAA-MM-DD.json
  data/index.json
  debug/*.html
  debug/*.limpio.html
"""

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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


# ============================================================
# UTILIDADES
# ============================================================

def http(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def get_json(url):
    return json.loads(http(url).decode("utf-8", "replace"))


def norm(texto):
    texto = unicodedata.normalize("NFD", texto or "").lower()
    texto = "".join(
        c for c in texto
        if unicodedata.category(c) != "Mn"
    )
    texto = re.sub(
        r"\b(fc|cf|cd|ud|sd|rc|ca|real|club|the|de|la|el)\b",
        " ",
        texto,
    )
    return re.sub(r"[^a-z0-9]", "", texto)


def texto_html(fragmento):
    fragmento = re.sub(
        r"<(script|style|svg|noscript)\b.*?</\1>",
        " ",
        fragmento or "",
        flags=re.I | re.S,
    )
    fragmento = re.sub(r"<[^>]+>", " ", fragmento)
    fragmento = html.unescape(fragmento)
    return re.sub(r"\s+", " ", fragmento).strip()


def fecha_hora_utc(fecha, hora, zona="Europe/Madrid"):
    try:
        if ZoneInfo:
            tz = ZoneInfo(zona)
        else:
            tz = timezone.utc

        dt = datetime.strptime(
            f"{fecha} {hora}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=tz)

        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def pais_por_dominio(url):
    dominio = urlparse(url).netloc.lower()
    sufijo = dominio.rsplit(".", 1)[-1]

    paises = {
        "es": "ES", "pl": "PL", "ca": "CA", "uk": "GB",
        "au": "AU", "mx": "MX", "de": "DE", "fr": "FR",
        "it": "IT", "br": "BR", "ar": "AR", "jp": "JP",
        "nl": "NL", "pt": "PT", "tr": "TR", "ie": "IE",
        "nz": "NZ", "za": "ZA", "se": "SE", "no": "NO",
        "dk": "DK", "be": "BE", "ch": "CH", "at": "AT",
    }

    return paises.get(sufijo, "Internacional")


def clave(evento):
    equipos = sorted(
        norm(x) for x in (evento.get("equipos") or []) if x
    )

    if len(equipos) == 2:
        return "|".join(equipos)

    return norm(evento.get("nombre"))


def normalizar_canales(canales):
    salida = []
    vistos = set()

    for canal in canales or []:
        nombre = str(canal.get("canal") or "").strip()
        pais = str(canal.get("pais") or "Internacional").strip()

        if not nombre:
            continue

        llave = (norm(nombre), pais.upper())

        if llave not in vistos:
            salida.append({"canal": nombre, "pais": pais})
            vistos.add(llave)

    return salida


# ============================================================
# THESPORTSDB
# ============================================================

def fuente_thesportsdb(fecha):
    url = (
        "https://www.thesportsdb.com/api/v1/json/"
        f"{TSDB_KEY}/eventstv.php?d={fecha}"
    )

    datos = get_json(url)
    salida = {}

    for item in datos.get("tvevents") or []:
        identificador = (
            item.get("idEvent")
            or item.get("strEvent")
        )

        evento = salida.setdefault(
            identificador,
            {
                "nombre": item.get("strEvent") or "",
                "deporte": item.get("strSport") or "Otros",
                "liga": item.get("strLeague") or "",
                "ts": (
                    item.get("strTimestamp")
                    or item.get("strTimeStamp")
                ),
                "equipos": [
                    item.get("strHomeTeam"),
                    item.get("strAwayTeam"),
                ],
                "canales": [],
            },
        )

        canal = item.get("strChannel") or item.get("strTVStation")

        if canal:
            evento["canales"].append({
                "canal": canal,
                "pais": item.get("strCountry") or "Internacional",
            })

    return list(salida.values())


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

    try:
        datos = get_json(
            "https://site.api.espn.com/apis/site/v2/"
            f"sports/{deporte}/{liga}/scoreboard"
            f"?dates={fecha.replace('-', '')}"
        )
    except Exception:
        return []

    ligas = datos.get("leagues") or [{}]
    nombre_liga = ligas[0].get("name", liga)
    salida = []

    for item in datos.get("events") or []:
        competicion = (item.get("competitions") or [{}])[0]
        canales = []

        for emision in competicion.get("geoBroadcasts") or []:
            nombre = (emision.get("media") or {}).get("shortName")

            if nombre:
                canales.append({
                    "canal": nombre,
                    "pais": (emision.get("region") or "US").upper(),
                })

        for emision in competicion.get("broadcasts") or []:
            for nombre in emision.get("names") or []:
                canales.append({"canal": nombre, "pais": "US"})

        equipos = [
            (competidor.get("team") or {}).get("displayName")
            for competidor in competicion.get("competitors") or []
        ]

        salida.append({
            "nombre": item.get("name") or "",
            "deporte": deporte.capitalize(),
            "liga": nombre_liga,
            "ts": item.get("date"),
            "equipos": equipos,
            "canales": normalizar_canales(canales),
        })

    return salida


def fuente_espn(fecha):
    with ThreadPoolExecutor(max_workers=8) as executor:
        resultados = executor.map(
            lambda par: _espn(par, fecha),
            ESPN,
        )

    return [evento for lista in resultados for evento in lista]


# ============================================================
# SOFASCORE
# ============================================================

def fuente_sofascore(fecha):
    base = "https://www.sofascore.com/api/v1"

    datos = get_json(
        f"{base}/sport/football/scheduled-events/{fecha}"
    )

    eventos = (datos.get("events") or [])[:120]

    def convertir(item):
        canales = []

        try:
            tv = get_json(
                f"{base}/tv/event/{item['id']}/country-channels"
            )

            nombres = {}

            if isinstance(tv.get("channels"), list):
                nombres = {
                    str(c.get("id")): c.get("name")
                    for c in tv["channels"]
                }

            for pais, canales_pais in (
                tv.get("countryChannels") or {}
            ).items():
                for canal in canales_pais:
                    nombre = (
                        canal.get("name")
                        if isinstance(canal, dict)
                        else nombres.get(str(canal))
                    )

                    if nombre:
                        canales.append({
                            "canal": nombre,
                            "pais": pais,
                        })

        except Exception:
            pass

        local = item.get("homeTeam") or {}
        visitante = item.get("awayTeam") or {}
        equipo_local = local.get("name") or ""
        equipo_visitante = visitante.get("name") or ""

        marca = item.get("startTimestamp")

        ts = (
            datetime.fromtimestamp(marca, timezone.utc).isoformat()
            if marca
            else None
        )

        return {
            "nombre": f"{equipo_local} vs {equipo_visitante}",
            "deporte": "Football",
            "equipos": [equipo_local, equipo_visitante],
            "ts": ts,
            "liga": (item.get("tournament") or {}).get("name", ""),
            "canales": normalizar_canales(canales),
        }

    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(convertir, eventos))


# ============================================================
# WEBS PARA PARSEO Y ARCHIVOS DE DEPURACIÓN
# ============================================================

WEBS = {
    "whatsportson": "https://www.whatsportson.com/",
    "livesportsin": "https://livesportsin.com/watch",
    "sportzap": "https://sportzap.tv/",
    "worldtvradio": "https://www.worldtvradio.com/",
    "calaverasports": "https://calaverasports.com/",
    "livesoccertv": "https://www.livesoccertv.com/es/",
    "relevo": "https://www.relevo.com/agenda-deportiva/",
    "sincroguia": (
        "https://sincroguia-tv.expansion.com/"
        "programacion-tv/retransmisiones-deportivas"
    ),
    "watchsportsguide": "https://watchsportsguide.com/",
    "futbolenlatv": "https://www.futbolenlatv.es/",
    "marca_tv": "https://www.marca.com/programacion-tv.html",
}


def limpiar_html(contenido):
    for etiqueta in (
        "script", "style", "svg", "nav",
        "noscript", "header", "footer", "head",
    ):
        contenido = re.sub(
            rf"<{etiqueta}\b.*?</{etiqueta}>",
            "",
            contenido,
            flags=re.S | re.I,
        )

    contenido = re.sub(r"<!--.*?-->", "", contenido, flags=re.S)

    contenido = re.sub(
        r"<img\b[^>]*?\balt=\"([^\"]*)\"[^>]*>",
        r'<img alt="\1">',
        contenido,
        flags=re.I,
    )

    contenido = re.sub(
        r"<img\b(?![^>]*alt=)[^>]*>",
        "",
        contenido,
        flags=re.I,
    )

    contenido = re.sub(
        r'\s(?:style|data-[a-z-]+|aria-[a-z-]+|'
        r'd|viewBox|fill|stroke[a-z-]*)="[^"]*"',
        "",
        contenido,
        flags=re.I,
    )

    return re.sub(r"\s+", " ", contenido)


def guardar_html_debug():
    carpeta = ROOT / "debug"
    carpeta.mkdir(parents=True, exist_ok=True)

    def descargar(elemento):
        nombre, url = elemento

        try:
            bruto = http(url, 30).decode("utf-8", "replace")

            (carpeta / f"{nombre}.html").write_text(
                bruto[:1_500_000],
                encoding="utf-8",
            )

            (carpeta / f"{nombre}.limpio.html").write_text(
                limpiar_html(bruto)[:150_000],
                encoding="utf-8",
            )

            return nombre, "HTML guardado"

        except Exception as error:
            mensaje = f"ERROR: {type(error).__name__}: {error}"

            (carpeta / f"{nombre}.html").write_text(
                mensaje,
                encoding="utf-8",
            )

            return nombre, mensaje

    with ThreadPoolExecutor(max_workers=6) as executor:
        for nombre, resultado in executor.map(
            descargar, WEBS.items()
        ):
            print(f"DEBUG {nombre}: {resultado}", file=sys.stderr)


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

_WS_CACHE = {}


def parsear_whatsportson(contenido):
    eventos = []

    articulos = re.findall(
        r"<article\b.*?</article>",
        contenido,
        flags=re.S | re.I,
    )

    for articulo in articulos:
        marca = re.search(
            r'href="/events/(\d+)/[^"]*?(\d{4}-\d{2}-\d{2})"',
            articulo,
        )

        hora = re.search(
            r"<time[^>]*>(\d{1,2}:\d{2})</time>",
            articulo,
            flags=re.I,
        )

        titulo = re.search(
            r'<p class="mt-1 [^"]*">(.*?)</p>',
            articulo,
            flags=re.S,
        )

        if not (marca and hora and titulo):
            continue

        emoji = re.search(
            r'<span class="text-sm leading-none">([^<]+)</span>',
            articulo,
            flags=re.S,
        )

        competicion = re.search(
            r'<a href="/competition/[^"]*"><span[^>]*>(.*?)</span></a>',
            articulo,
            flags=re.S,
        )

        detalle = re.search(
            r'<p class="mt-0\.5 text-xs[^"]*">(.*?)</p>',
            articulo,
            flags=re.S,
        )

        canales = []

        enlaces = re.findall(
            r'<a href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>(.*?)</a>',
            articulo,
            flags=re.S | re.I,
        )

        for url, interior in enlaces:
            alt = re.search(r'alt="([^"]*)"', interior, flags=re.I)

            nombre = (
                html.unescape(alt.group(1)).strip()
                if alt and alt.group(1).strip()
                else texto_html(re.sub(r"FREE", "", interior))
            )

            dominio = urlparse(url).netloc.replace("www.", "")

            if not nombre and dominio:
                nombre = dominio

            if nombre and "whatsportson.com" not in dominio:
                canales.append({
                    "canal": nombre,
                    "pais": pais_por_dominio(url),
                })

        titulo_texto = texto_html(titulo.group(1))
        equipos = (
            [x.strip() for x in re.split(r"\s+v\s+", titulo_texto)]
            if " v " in titulo_texto
            else []
        )

        emoji_texto = (
            emoji.group(1).strip().replace("\ufe0f", "")
            if emoji
            else ""
        )

        fecha = marca.group(2)
        hora_texto = hora.group(1)

        eventos.append({
            "fecha": fecha,
            "nombre": titulo_texto,
            "deporte": EMOJI_DEPORTE.get(emoji_texto, "Otros"),
            "liga": texto_html(competicion.group(1)) if competicion else "",
            "ts": fecha_hora_utc(fecha, hora_texto, "Europe/London"),
            "equipos": equipos if len(equipos) == 2 else [],
            "canales": normalizar_canales(canales),
            "detalle": texto_html(detalle.group(1)) if detalle else "",
            "id": marca.group(1),
        })

    salida = []
    vistos = set()

    for evento in eventos:
        if evento["id"] not in vistos:
            vistos.add(evento["id"])
            salida.append(evento)

    return salida


def fuente_whatsportson(fecha):
    if "eventos" not in _WS_CACHE:
        contenido = http(
            WEBS["whatsportson"], 30
        ).decode("utf-8", "replace")

        _WS_CACHE["eventos"] = parsear_whatsportson(contenido)

    return [
        {
            k: v for k, v in evento.items()
            if k not in ("fecha", "id", "detalle")
        }
        for evento in _WS_CACHE["eventos"]
        if evento["fecha"] == fecha
    ]


# ============================================================
# RELEVO
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

_RELEVO_CACHE = {}


def parsear_relevo(contenido):
    eventos = []

    grupos = re.findall(
        r'<div class="sports-agenda-group (sport-[\w-]+)">(.*?)</ul>',
        contenido,
        flags=re.S,
    )

    for clase, bloque in grupos:
        nombre_liga = re.search(
            r'sports-agenda-group-name">(.*?)</span>',
            bloque,
            flags=re.S,
        )

        liga = texto_html(nombre_liga.group(1)) if nombre_liga else ""

        elementos = re.findall(
            r'<li class="sports-agenda-event">(.*?)</li>',
            bloque,
            flags=re.S,
        )

        for elemento in elementos:
            marca = re.search(
                r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})',
                elemento,
            )

            tv = re.search(
                r'<span class="sports-agenda-tv">(.*?)</span>',
                elemento,
                flags=re.S,
            )

            if not marca:
                continue

            resto = re.sub(
                r'<span class="sports-agenda-tv">.*?</span>',
                "",
                elemento,
                flags=re.S,
            )
            resto = re.sub(
                r'<a class="sports-agenda-pick".*?</a>',
                "",
                resto,
                flags=re.S,
            )
            resto = re.sub(
                r"<time.*?</time>",
                "",
                resto,
                flags=re.S,
            )
            resto = re.sub(
                r'<span class="sports-agenda-time[^"]*">.*?</span>',
                "",
                resto,
                flags=re.S,
            )

            texto = texto_html(resto)
            partes = [
                x.strip()
                for x in re.split(r"\s[–-]\s", texto)
            ]

            equipos = partes if len(partes) == 2 else []

            nombre = (
                f"{partes[0]} vs {partes[1]}"
                if equipos
                else f"{liga}: {texto}"
            )

            fecha, hora = marca.groups()
            canal = texto_html(tv.group(1)) if tv else ""

            eventos.append({
                "fecha": fecha,
                "nombre": nombre,
                "deporte": SPORT_RELEVO.get(clase, "Otros"),
                "liga": liga,
                "ts": fecha_hora_utc(fecha, hora),
                "equipos": equipos,
                "canales": (
                    [{"canal": canal, "pais": "ES"}]
                    if canal else []
                ),
            })

    return eventos


def fuente_relevo(fecha):
    if "eventos" not in _RELEVO_CACHE:
        contenido = http(
            WEBS["relevo"], 30
        ).decode("utf-8", "replace")

        _RELEVO_CACHE["eventos"] = parsear_relevo(contenido)

    return [
        {k: v for k, v in evento.items() if k != "fecha"}
        for evento in _RELEVO_CACHE["eventos"]
        if evento["fecha"] == fecha
    ]


# ============================================================
# FÚTBOL EN LA TELE
# Extractor experimental: depende de la estructura HTML de la web.
# ============================================================

_FUTBOLENLATV_CACHE = {}


def parsear_futbolenlatv(contenido):
    eventos = []

    # Busca bloques HTML con indicios de partido.
    bloques = re.findall(
        r"<(?:article|div)\b[^>]*>.*?</(?:article|div)>",
        contenido,
        flags=re.S | re.I,
    )

    # Añade bloques mayores conocidos por sus clases.
    bloques.extend(
        re.findall(
            r'<div\b[^>]*class="[^"]*(?:partido|match|fixture|event)[^"]*"'
            r'[^>]*>.*?</div>',
            contenido,
            flags=re.S | re.I,
        )
    )

    vistos = set()

    for bloque in bloques:
        texto = texto_html(bloque)

        if len(texto) < 12 or len(texto) > 5000:
            continue

        marca_hora = re.search(
            r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
            texto,
        )

        if not marca_hora:
            continue

        # Busca fecha explícita dentro del bloque.
        marca_fecha = re.search(
            r"\b(20\d{2})-(\d{2})-(\d{2})\b",
            bloque,
        )

        if marca_fecha:
            fecha = "-".join(marca_fecha.groups())
        else:
            # La página principal suele mostrar la programación del día.
            fecha = datetime.now(timezone.utc).date().isoformat()

        hora = f"{int(marca_hora.group(1)):02d}:{marca_hora.group(2)}"

        # Extrae posibles canales de enlaces e imágenes.
        canales = []

        for enlace in re.findall(
            r"<a\b[^>]*>.*?</a>",
            bloque,
            flags=re.S | re.I,
        ):
            nombre = texto_html(enlace)

            alt = re.search(r'\balt=["\']([^"\']+)["\']', enlace, re.I)

            if alt:
                nombre = html.unescape(alt.group(1)).strip()

            nombre = re.sub(r"\s+", " ", nombre).strip()

            if (
                nombre
                and len(nombre) <= 90
                and not re.fullmatch(r"\d{1,2}:\d{2}", nombre)
                and norm(nombre) not in {
                    "ver", "mas", "partido", "futbol", "hoy",
                }
            ):
                canales.append({"canal": nombre, "pais": "ES"})

        canales = normalizar_canales(canales)

        # Evita inventar equipos: conserva el texto cuando no se
        # pueden identificar con seguridad.
        nombre = texto[:180]
        clave_evento = (fecha, hora, norm(nombre))

        if clave_evento in vistos:
            continue

        vistos.add(clave_evento)

        eventos.append({
            "nombre": nombre,
            "deporte": "Football",
            "liga": "",
            "ts": fecha_hora_utc(fecha, hora),
            "equipos": [],
            "canales": canales,
        })

    return eventos


def fuente_futbolenlatv(fecha):
    if "eventos" not in _FUTBOLENLATV_CACHE:
        contenido = http(
            WEBS["futbolenlatv"], 30
        ).decode("utf-8", "replace")

        _FUTBOLENLATV_CACHE["eventos"] = (
            parsear_futbolenlatv(contenido)
        )

    return [
        evento
        for evento in _FUTBOLENLATV_CACHE["eventos"]
        if (evento.get("ts") or "").startswith(fecha)
    ]


# ============================================================
# MARCA TV
# Descarga y parser experimental. Si no reconoce bloques válidos,
# devuelve cero eventos en lugar de inventar información.
# ============================================================

_MARCA_CACHE = {}


def parsear_marca_tv(contenido, fecha):
    eventos = []

    # Solo intenta extraer bloques que contengan hora y señales
    # de contenido deportivo o televisivo.
    bloques = re.findall(
        r"<(?:article|li)\b[^>]*>.*?</(?:article|li)>",
        contenido,
        flags=re.S | re.I,
    )

    for bloque in bloques:
        texto = texto_html(bloque)

        marca_hora = re.search(
            r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
            texto,
        )

        if not marca_hora:
            continue

        if not re.search(
            r"f[uú]tbol|tenis|baloncesto|baloncesto|"
            r"balonmano|ciclismo|motor|golf|rugby|"
            r"deporte|liga|copa|champions|dazn|movistar|"
            r"eurosport|teledeporte",
            texto,
            flags=re.I,
        ):
            continue

        hora = (
            f"{int(marca_hora.group(1)):02d}:"
            f"{marca_hora.group(2)}"
        )

        canales = []

        for enlace in re.findall(
            r"<a\b[^>]*>.*?</a>",
            bloque,
            flags=re.S | re.I,
        ):
            nombre = texto_html(enlace)
            alt = re.search(
                r'\balt=["\']([^"\']+)["\']',
                enlace,
                flags=re.I,
            )

            if alt:
                nombre = html.unescape(alt.group(1)).strip()

            if nombre and len(nombre) <= 80:
                canales.append({"canal": nombre, "pais": "ES"})

        eventos.append({
            "nombre": texto[:180],
            "deporte": "Otros",
            "liga": "Programación TV",
            "ts": fecha_hora_utc(fecha, hora),
            "equipos": [],
            "canales": normalizar_canales(canales),
        })

    return eventos


def fuente_marca_tv(fecha):
    if "eventos" not in _MARCA_CACHE:
        contenido = http(
            WEBS["marca_tv"], 30
        ).decode("utf-8", "replace")

        _MARCA_CACHE["eventos"] = parsear_marca_tv(
            contenido,
            fecha,
        )

    return [
        evento
        for evento in _MARCA_CACHE["eventos"]
        if (evento.get("ts") or "").startswith(fecha)
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
    "FutbolEnLaTV": fuente_futbolenlatv,
    "MARCA_TV": fuente_marca_tv,
}


# ============================================================
# UNIÓN DE EVENTOS Y ESTADÍSTICAS
# ============================================================

def dia(fecha):
    unidos = {}
    estado = {}
    eventos_por_fuente = {}

    with ThreadPoolExecutor(
        max_workers=max(1, len(FUENTES))
    ) as executor:
        futuros = {
            nombre: executor.submit(funcion, fecha)
            for nombre, funcion in FUENTES.items()
        }

        for nombre, futuro in futuros.items():
            try:
                lista = futuro.result()
                eventos_por_fuente[nombre] = lista
                estado[nombre] = f"{len(lista)} eventos"

            except Exception as error:
                eventos_por_fuente[nombre] = []
                estado[nombre] = (
                    f"error: {type(error).__name__}: {error}"
                )

    claves_por_fuente = {}

    for nombre, lista in eventos_por_fuente.items():
        claves = set()

        for evento in lista:
            llave = clave(evento)

            if not llave:
                continue

            claves.add(llave)

            unido = unidos.setdefault(
                llave,
                {
                    **evento,
                    "canales": [],
                    "fuentes": [],
                },
            )

            if nombre not in unido["fuentes"]:
                unido["fuentes"].append(nombre)

            canales_existentes = {
                (
                    norm(c.get("canal")),
                    str(c.get("pais", "")).upper(),
                )
                for c in unido["canales"]
            }

            for canal in evento.get("canales") or []:
                canal_nombre = canal.get("canal", "")
                pais = canal.get("pais", "Internacional")
                llave_canal = (norm(canal_nombre), pais.upper())

                if canal_nombre and llave_canal not in canales_existentes:
                    unido["canales"].append({
                        "canal": canal_nombre,
                        "pais": pais,
                    })
                    canales_existentes.add(llave_canal)

            if not unido.get("ts"):
                unido["ts"] = evento.get("ts")

            if not unido.get("liga"):
                unido["liga"] = evento.get("liga", "")

            if not unido.get("deporte"):
                unido["deporte"] = evento.get("deporte", "Otros")

        claves_por_fuente[nombre] = claves

    eventos_finales = sorted(
        unidos.values(),
        key=lambda evento: evento.get("ts") or "",
    )

    for evento in eventos_finales:
        evento["canales"] = normalizar_canales(evento.get("canales"))
        evento.pop("equipos", None)

    # Estadísticas por fuente.
    estadisticas = {}

    todas_las_claves = set(unidos.keys())

    for nombre, lista in eventos_por_fuente.items():
        claves = claves_por_fuente.get(nombre, set())
        exclusivas = [
            llave for llave in claves
            if len(unidos[llave].get("fuentes", [])) == 1
        ]
        compartidas = [
            llave for llave in claves
            if len(unidos[llave].get("fuentes", [])) > 1
        ]

        eventos_con_canales = 0
        canales_por_pais = {}
        canales_es = 0
        pares_canal_evento = set()

        for evento in lista:
            canales = normalizar_canales(evento.get("canales"))

            if canales:
                eventos_con_canales += 1

            for canal in canales:
                pais = canal.get("pais", "Internacional")
                nombre_canal = canal.get("canal", "")

                canales_por_pais[pais] = (
                    canales_por_pais.get(pais, 0) + 1
                )

                if pais.upper() == "ES":
                    canales_es += 1

                pares_canal_evento.add(
                    (clave(evento), norm(nombre_canal), pais.upper())
                )

        estadisticas[nombre] = {
            "eventos_recibidos": len(lista),
            "eventos_distintos": len(claves),
            "eventos_exclusivos": len(exclusivas),
            "eventos_compartidos": len(compartidas),
            "eventos_con_canales": eventos_con_canales,
            "canales_evento_distintos": len(pares_canal_evento),
            "canales_es_evento_distintos": canales_es,
            "canales_por_pais": canales_por_pais,
            "estado": estado.get(nombre, ""),
        }

    eventos_con_canales = sum(
        1 for evento in eventos_finales
        if evento.get("canales")
    )

    eventos_sin_canales = len(eventos_finales) - eventos_con_canales

    eventos_con_canales_es = sum(
        1 for evento in eventos_finales
        if any(
            str(canal.get("pais", "")).upper() == "ES"
            for canal in evento.get("canales", [])
        )
    )

    resumen = {
        "eventos_finales": len(eventos_finales),
        "eventos_con_canales": eventos_con_canales,
        "eventos_sin_canales": eventos_sin_canales,
        "eventos_con_canales_es": eventos_con_canales_es,
        "fuentes_activas": len(FUENTES),
        "fuentes_con_resultados": sum(
            1 for lista in eventos_por_fuente.values() if lista
        ),
    }

    return {
        "fecha": fecha,
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "estado": estado,
        "resumen": resumen,
        "estadisticas_fuentes": estadisticas,
        "eventos": eventos_finales,
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
        try:
            resultado = dia(fecha)

            destino = ROOT / "data" / f"{fecha}.json"
            destino.write_text(
                json.dumps(resultado, ensure_ascii=False),
                encoding="utf-8",
            )

            print(
                fecha,
                json.dumps(resultado["estado"], ensure_ascii=False),
                file=sys.stderr,
            )

        except Exception as error:
            print(
                f"ERROR GENERAL {fecha}: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )

    (ROOT / "data" / "index.json").write_text(
        json.dumps({"fechas": fechas}, ensure_ascii=False),
        encoding="utf-8",
    )

    for archivo in (ROOT / "data").glob("20*.json"):
        if archivo.stem not in fechas:
            archivo.unlink()

    guardar_html_debug()
