"""Descubre todos los productos de una web de supermercado.

1. Sitemaps (lo más rápido y respetuoso): los indicados en robots.txt, o /sitemap.xml.
   Se priorizan los sitemaps de productos (p. ej. «sitemap_0-product.xml»).
2. Si no hay sitemap útil, recorre la web siguiendo categorías y paginación.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from collections import deque
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .descargador import BloqueoAntiBot, Descargador
from .extractor import NO_PRODUCTO, PATRON_FUERTE, extraer_enlaces_productos

LOC_RE = re.compile(rb"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]]+?)\s*(?:\]\]>)?\s*</loc>", re.I)
SITEMAP_PRODUCTOS = re.compile(r"product|producto|articulo|item|pdp|sku", re.I)
SITEMAP_INUTIL = re.compile(r"image|imagen|categor|store|tienda|blog|page|pagina|content|"
                            r"collection|brand|marca|receta|recipe|static|video|news|noticia", re.I)
NO_RECORRER = re.compile(r"login|logout|cuenta|account|carrito|cart|checkout|ayuda|help|legal|"
                         r"cookie|privacidad|privacy|contact|blog|receta|empleo|trabaja|tiendas|"
                         r"store-locator|\.(pdf|jpg|jpeg|png|gif|webp|svg|zip)$", re.I)


def _sin_tildes(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def cumple_filtro(url: str, filtro: str | None) -> bool:
    """Todas las palabras del filtro deben aparecer en la URL (sin tildes ni mayúsculas)."""
    if not filtro:
        return True
    destino = _sin_tildes(url).replace("-", " ").replace("_", " ")
    return all(p in destino for p in _sin_tildes(filtro).split())


def _nombre(url_sitemap: str) -> str:
    """Nombre del fichero sin la palabra «sitemap» (que contiene «item» y confunde)."""
    return re.sub(r"site-?map", "", url_sitemap.rsplit("/", 1)[-1], flags=re.I)


def _mismo_sitio(a: str, b: str) -> bool:
    quita = lambda h: (h or "").lower().removeprefix("www.")  # noqa: E731
    return quita(urlparse(a).netloc) == quita(urlparse(b).netloc)


def _leer_sitemap(d: Descargador, url: str, base: str) -> list[str]:
    try:
        return [m.decode("utf8", "replace").strip() for m in LOC_RE.findall(d.contenido(url))]
    except BloqueoAntiBot:
        raise
    except Exception:  # noqa: BLE001
        # Algunas webs anuncian el sitemap con otro host (con/sin www): probar con el de la web
        u, b = urlparse(url), urlparse(base)
        if u.netloc != b.netloc and _mismo_sitio(url, base):
            try:
                alt = u._replace(netloc=b.netloc).geturl()
                return [m.decode("utf8", "replace").strip() for m in LOC_RE.findall(d.contenido(alt))]
            except Exception:  # noqa: BLE001
                return []
        return []


def desde_sitemaps(d: Descargador, inicio: str, maximo: int, filtro: str | None,
                   log: Callable[[str], None], parar: threading.Event | None = None,
                   max_ficheros: int = 40) -> list[str]:
    base = "{0.scheme}://{0.netloc}".format(urlparse(inicio))
    rp = d.robots(inicio)
    iniciales = list((rp.site_maps() if rp else None) or [])
    if not iniciales:
        iniciales = [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    log(f"Buscando sitemaps: {', '.join(iniciales[:3])}")

    pendientes = deque(iniciales)
    vistos, productos, genericas = set(), [], []
    leidos = 0
    while pendientes and leidos < max_ficheros and len(productos) < maximo:
        if parar and parar.is_set():
            break
        sm = pendientes.popleft()
        if sm in vistos:
            continue
        vistos.add(sm)
        locs = _leer_sitemap(d, sm, inicio)
        leidos += 1
        if not locs:
            continue
        hijos = [u for u in locs if re.search(r"\.xml(\.gz)?(\?.*)?$", u, re.I)]
        if hijos and len(hijos) >= len(locs) / 2:  # es un índice de sitemaps
            prod = [h for h in hijos if SITEMAP_PRODUCTOS.search(_nombre(h))]
            resto = [h for h in hijos if h not in prod and not SITEMAP_INUTIL.search(_nombre(h))]
            # primero los de productos; los genéricos solo si no hay de productos
            pendientes.extendleft(reversed(prod))
            if not prod:
                pendientes.extend(resto)
            continue
        es_de_productos = bool(SITEMAP_PRODUCTOS.search(_nombre(sm)))
        nuevos = 0
        for u in locs:
            if not _mismo_sitio(u, inicio) or not cumple_filtro(u, filtro):
                continue
            ruta = urlparse(u).path
            if es_de_productos:
                productos.append(u)
                nuevos += 1
            elif PATRON_FUERTE.search(ruta) and not NO_PRODUCTO.search(ruta):
                genericas.append(u)
        if nuevos:
            log(f" · {sm.rsplit('/', 1)[-1]}: {nuevos} productos")
        if len(productos) >= maximo:
            break
    if not productos and genericas:
        log(f" · {len(genericas)} posibles productos en sitemaps generales")
        productos = genericas
    return list(dict.fromkeys(productos))[:maximo]


def recorriendo(d: Descargador, inicio: str, maximo: int, filtro: str | None,
                log: Callable[[str], None], parar: threading.Event | None = None,
                max_paginas: int = 60, patron: str | None = None) -> list[str]:
    """Recorre la web (portada → categorías → paginación) recogiendo enlaces de producto."""
    cola, vistas, productos = deque([inicio]), {inicio}, []
    paginas = 0
    while cola and paginas < max_paginas and len(productos) < maximo:
        if parar and parar.is_set():
            break
        url = cola.popleft()
        try:
            html = d.html(url)
        except BloqueoAntiBot:
            raise
        except Exception as e:  # noqa: BLE001
            log(f" ! {url}: {e}")
            continue
        paginas += 1
        encontrados = [u for u in extraer_enlaces_productos(html, url, patron) if cumple_filtro(u, filtro)]
        antes = len(productos)
        productos = list(dict.fromkeys(productos + encontrados))
        log(f" · página {paginas}: {url} → {len(productos) - antes} productos nuevos (total {len(productos)})")
        conocidos = set(productos)
        for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
            sig = urljoin(url, a["href"]).split("#")[0]
            ruta = urlparse(sig).path
            if (sig in vistas or sig in conocidos or not _mismo_sitio(sig, inicio)
                    or not sig.startswith("http") or NO_RECORRER.search(sig)
                    or (PATRON_FUERTE.search(ruta) and not NO_PRODUCTO.search(ruta))):
                continue
            vistas.add(sig)
            cola.append(sig)
    return productos[:maximo]


def descubrir_productos(d: Descargador, inicio: str, maximo: int = 300, filtro: str | None = None,
                        log: Callable[[str], None] = print, parar: threading.Event | None = None,
                        patron: str | None = None) -> list[str]:
    log(f"Escaneando {urlparse(inicio).netloc}" + (f" (filtro: «{filtro}»)" if filtro else ""))
    productos = desde_sitemaps(d, inicio, maximo, filtro, log, parar)
    if productos:
        log(f"→ {len(productos)} productos encontrados en el sitemap")
        return productos
    log("Sin sitemap de productos: recorro la web por categorías…")
    productos = recorriendo(d, inicio, maximo, filtro, log, parar, patron=patron)
    log(f"→ {len(productos)} productos encontrados recorriendo la web")
    return productos
