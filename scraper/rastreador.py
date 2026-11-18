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


# ---------------------------------------------------------------------------
# Categorías completas
# ---------------------------------------------------------------------------
PARAM_PAGINA = re.compile(r"[?&](page|p|pagina|pag|pageNumber|pg|numPagina)=(\d+)", re.I)
TOTAL_RE = [
    re.compile(r'"currentCategory"\s*:\s*\{[^{}]*?"productCount"\s*:\s*(\d+)'),   # Ocado (Alcampo)
    re.compile(r'"(?:totalCount|totalResults|numFound|total_products|productCount)"\s*:\s*(\d+)'),
    re.compile(r"(\d[\d.]{0,7})\s*(?:resultados|productos|artículos|articulos)\b", re.I),
]


def total_declarado(html: str) -> int | None:
    """Cuántos productos dice tener la categoría (si la página lo indica)."""
    for rx in TOTAL_RE:
        if m := rx.search(html):
            try:
                n = int(m.group(1).replace(".", ""))
                if 0 < n < 200000:
                    return n
            except ValueError:
                pass
    return None


def _con_parametros(url: str, **params) -> str:
    from urllib.parse import parse_qsl, urlencode
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q.update({k: str(v) for k, v in params.items()})
    return u._replace(query=urlencode(q)).geturl()


def _prefijo_categoria(url: str) -> str:
    """/categories/frescos/jamones/OC151001 -> /categories/frescos/jamones (quita un id final)."""
    ruta = urlparse(url).path.rstrip("/")
    ultimo = ruta.rsplit("/", 1)[-1]
    if re.search(r"\d", ultimo) and ruta.count("/") > 1:
        ruta = ruta.rsplit("/", 1)[0]
    return ruta.lower()


def productos_de_categoria(d: Descargador, url: str, maximo: int = 200,
                           log: Callable[[str], None] = print, parar: threading.Event | None = None,
                           patron: str | None = None, max_paginas: int = 60) -> list[str]:
    """Todos los productos de una categoría, aunque la web los muestre por páginas,
    con «cargar más» o repartidos en subcategorías."""
    productos: list[str] = []
    paginas = 0

    def añadir(html, pagina_url) -> int:
        nonlocal productos
        antes = len(productos)
        productos = list(dict.fromkeys(productos + extraer_enlaces_productos(html, pagina_url, patron)))
        return len(productos) - antes

    def basta() -> bool:
        return len(productos) >= maximo or bool(parar and parar.is_set())

    html = d.html(url)
    paginas += 1
    añadir(html, url)
    total = total_declarado(html)
    log(f" · página 1: {len(productos)} productos" + (f" (la categoría dice tener {total})" if total else ""))

    def completo() -> bool:
        return basta() or bool(total and len(productos) >= total)

    # 1) Webs que admiten pedir muchos productos de una vez (Salesforce: ?start=0&sz=N, p. ej. Ahorramas)
    if not completo() and re.search(r"[?&;]sz=\d+|Search-UpdateGrid|Search-ShowAjax", html):
        inicio = 0
        lote = min(max(maximo, 50), 500)
        while not completo() and paginas < max_paginas:
            pagina = _con_parametros(url, start=inicio, sz=lote)
            nuevos = añadir(d.html(pagina), pagina)
            paginas += 1
            log(f" · productos {inicio + 1}-{inicio + lote}: {nuevos} nuevos (total {len(productos)})")
            if not nuevos:
                break
            inicio += lote

    # 2) Paginación clásica (?page=2, «siguiente»)
    if not completo():
        soup = BeautifulSoup(html, "lxml")
        siguiente = soup.select_one('link[rel=next][href], a[rel=next][href]')
        numeradas = [m for a in soup.find_all("a", href=True) if (m := PARAM_PAGINA.search(a["href"]))]
        if siguiente or numeradas:
            nombre = numeradas[0].group(1) if numeradas else None
            n = 2
            pagina = urljoin(url, siguiente["href"]) if siguiente else _con_parametros(url, **{nombre: n})
            while pagina and not completo() and paginas < max_paginas:
                h = d.html(pagina)
                paginas += 1
                nuevos = añadir(h, pagina)
                log(f" · página {n}: {nuevos} nuevos (total {len(productos)})")
                if not nuevos:
                    break
                n += 1
                sig = BeautifulSoup(h, "lxml").select_one('link[rel=next][href], a[rel=next][href]')
                pagina = urljoin(pagina, sig["href"]) if sig else (_con_parametros(url, **{nombre: n}) if nombre else None)

    # 3) Subcategorías: la web solo enseña una parte; cada subcategoría es más pequeña (p. ej. Alcampo)
    if not completo():
        from urllib.parse import unquote

        def normal(u: str) -> str:
            return unquote(unquote(u.split("#")[0].split("?")[0])).rstrip("/")

        prefijo = unquote(unquote(_prefijo_categoria(url)))
        cola, vistas = deque(), {normal(url).lower()}

        def encolar(h, desde):
            for a in BeautifulSoup(h, "lxml").find_all("a", href=True):
                sub = normal(urljoin(desde, a["href"]))  # sin ?orden=… (a menudo prohibido en robots.txt)
                ruta = urlparse(sub).path.lower()
                if (sub.lower() not in vistas and _mismo_sitio(sub, url)
                        and ruta.startswith(prefijo + "/") and ruta != prefijo
                        and not (PATRON_FUERTE.search(ruta) and not NO_PRODUCTO.search(ruta))):
                    vistas.add(sub.lower())
                    cola.append(sub)

        encolar(html, url)
        if cola:
            log(f" · {len(cola)} subcategorías para completar")
        while cola and not completo() and paginas < max_paginas:
            sub = cola.popleft()
            try:
                h = d.html(sub)
            except BloqueoAntiBot:
                raise
            except Exception as e:  # noqa: BLE001
                log(f"   ! {sub}: {e}")
                continue
            paginas += 1
            nuevos = añadir(h, sub)
            nombre = urlparse(sub).path.rstrip("/").rsplit("/", 2)
            nombre = nombre[-2] if re.search(r"\d", nombre[-1]) and len(nombre) > 2 else nombre[-1]
            log(f" · {nombre[:40]}: {nuevos} nuevos (total {len(productos)})")
            encolar(h, sub)

    if total and len(productos) < total and not basta():
        log(f" · Encontrados {len(productos)} de los {total} que indica la web (el resto pueden ser productos "
            "agotados o que la web solo carga con JavaScript; prueba el «modo navegador»)")
    return productos[:maximo]
