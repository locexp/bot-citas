"""Árbol de categorías de una web de supermercado, para elegirlas desde la app o Telegram.

Fuentes, por orden:
1. Sitemaps de categorías (p. ej. «sitemap_2-category.xml», «sitemap-categories-part1.xml»).
2. Enlaces de categoría del menú de la portada (p. ej. Lidl: «/c/...»).
Los nombres salen del texto de los enlaces de la portada o, si no, de la propia dirección.
La jerarquía se deduce de la ruta: /frescos/carnes/pollo/ cuelga de /frescos/carnes/.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .descargador import Descargador
from .extractor import NO_PRODUCTO, PATRON_FUERTE
from .rastreador import _leer_sitemap, _mismo_sitio

# Supermercados con los que se ha probado (se muestran como atajos)
SUPERMERCADOS = [
    {"nombre": "Ahorramas", "url": "https://www.ahorramas.com/"},
    {"nombre": "Alcampo", "url": "https://www.compraonline.alcampo.es/"},
    {"nombre": "Lidl", "url": "https://www.lidl.es/"},
]

RAIZ_TRANSPARENTE = {"categories", "category", "categorias", "categoria", "c", "cat", "shop", "tienda"}
NOMBRES_RAIZ = {"promotions": "Promociones", "marcas": "Marcas", "brands": "Marcas"}
SITEMAP_CATEGORIAS = re.compile(r"categor", re.I)
ENLACE_CATEGORIA = re.compile(r"/(c|categor\w*|cat)/|/categories/|-c\d+|/s\d{6,}", re.I)
NO_CATEGORIA = re.compile(r"login|cuenta|account|carrito|cart|ayuda|help|legal|cookie|privacidad|"
                          r"contact|blog|receta|empleo|tiendas|store|folleto|faq", re.I)


# Páginas de los menús que no son categorías de productos
NOMBRE_NO_CATEGORIA = re.compile(
    r"consejo|tutorial|compliance|newsletter|registro|t[eé]rminos|condiciones|protecci[oó]n|"
    r"declaraci[oó]n|informaci[oó]n|ganador|premiad|cup[oó]n|tarjeta|puntos|lidl plus|inspiraci|"
    r"ideas|cocina con|accesibilidad|privacidad|cookies|ayuda|contacto|empleo|sostenib|"
    r"nuestra|qui[eé]nes|sorteo|app\b|descarga", re.I)


@dataclass
class Categoria:
    id: int
    nombre: str
    url: str | None
    padre: int | None = None
    hijos: list[int] = field(default_factory=list)

    def a_dict(self) -> dict:
        return {"id": self.id, "n": self.nombre, "u": self.url, "p": self.padre, "h": len(self.hijos)}


def _clave(url: str) -> list[str]:
    """Tramos de la ruta que dan la jerarquía (sin ids finales ni raíces tipo /categories/)."""
    tramos = [unquote(unquote(t)).lower() for t in urlparse(url).path.strip("/").split("/") if t]
    if len(tramos) > 1 and re.search(r"\d", tramos[-1]) and not re.search(r"[a-z]{4,}", tramos[-1]):
        tramos = tramos[:-1]  # .../jamones-y-paletas/OC151001 -> sin el id
    elif len(tramos) > 1 and re.fullmatch(r"[a-z]{0,3}\d{4,}", tramos[-1]):
        tramos = tramos[:-1]  # Lidl: /c/frutas/s10089404
    if tramos and tramos[0] in RAIZ_TRANSPARENTE:
        tramos = tramos[1:]
    return [re.sub(r"\.html?$", "", t) for t in tramos]


def limpiar_nombre(t: str) -> str:
    t = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]", "", t)  # guiones blandos e invisibles
    t = re.sub(r"\s+", " ", t).strip()
    palabras = t.split(" ")
    mitad = len(palabras) // 2
    if len(palabras) % 2 == 0 and mitad and palabras[:mitad] == palabras[mitad:]:
        t = " ".join(palabras[:mitad])  # «Bebé Bebé» -> «Bebé»
    return t[:80]


def _nombre_desde_ruta(tramo: str) -> str:
    t = re.sub(r"[-_]+", " ", tramo).strip()
    return (NOMBRES_RAIZ.get(tramo) or (t[:1].upper() + t[1:]) or tramo)[:80]


class ArbolCategorias:
    def __init__(self, sitio: str):
        self.sitio = sitio
        self.nodos: list[Categoria] = []
        self._por_clave: dict[tuple, int] = {}
        self.creado = time.time()

    def _nodo(self, clave: tuple, nombre: str | None, url: str | None) -> int:
        if clave in self._por_clave:
            n = self.nodos[self._por_clave[clave]]
            if url and not n.url:
                n.url = url
            if nombre and (n.nombre == _nombre_desde_ruta(clave[-1]) or not n.nombre):
                n.nombre = nombre
            return n.id
        padre = self._nodo(clave[:-1], None, None) if len(clave) > 1 else None
        n = Categoria(len(self.nodos), nombre or _nombre_desde_ruta(clave[-1]), url, padre)
        self.nodos.append(n)
        self._por_clave[clave] = n.id
        if padre is not None:
            self.nodos[padre].hijos.append(n.id)
        return n.id

    def añadir(self, url: str, nombre: str | None):
        clave = tuple(_clave(url))
        if clave:
            self._nodo(clave, nombre, url)

    def ordenar(self):
        for n in self.nodos:
            n.hijos.sort(key=lambda i: self.nodos[i].nombre.lower())

    def raices(self) -> list[Categoria]:
        r = [n for n in self.nodos if n.padre is None]
        # Primero las que tienen subcategorías y no son marcas/promociones
        return sorted(r, key=lambda n: (n.nombre in NOMBRES_RAIZ.values(), n.nombre.lower()))

    def a_lista(self) -> list[dict]:
        return [n.a_dict() for n in self.nodos]

    def ruta(self, i: int) -> str:
        partes = []
        while i is not None:
            partes.append(self.nodos[i].nombre)
            i = self.nodos[i].padre
        return " › ".join(reversed(partes))

    def hijos_de(self, i: int | None) -> list[Categoria]:
        return self.raices() if i is None else [self.nodos[h] for h in self.nodos[i].hijos]

    def urls_de(self, i: int, maximo: int = 60) -> list[str]:
        """URL de la categoría o, si no tiene, las de sus subcategorías (para descargar «todo X»)."""
        n = self.nodos[i]
        if n.url:
            return [n.url]
        urls = []
        for h in n.hijos:
            urls += self.urls_de(h, maximo - len(urls))
            if len(urls) >= maximo:
                break
        return urls[:maximo]

    def buscar(self, texto: str, maximo: int = 40) -> list[Categoria]:
        from .rastreador import _sin_tildes
        palabras = _sin_tildes(texto).split()
        if not palabras:
            return []
        encontrados = [n for n in self.nodos if all(p in _sin_tildes(n.nombre) for p in palabras)]
        # primero las más generales (menos profundas) y con más subcategorías
        encontrados.sort(key=lambda n: (self.ruta(n.id).count("›"), -len(n.hijos), n.nombre.lower()))
        return encontrados[:maximo]


def obtener_categorias(d: Descargador, sitio: str, log=lambda m: None, max_sitemaps: int = 15) -> ArbolCategorias:
    base = "{0.scheme}://{0.netloc}".format(urlparse(sitio))
    arbol = ArbolCategorias(sitio)

    # Nombres: texto de los enlaces de la portada
    nombres: dict[str, str] = {}
    candidatas_menu: list[str] = []
    try:
        html = d.html(sitio)
        soup = BeautifulSoup(html, "lxml")
        menu = soup.select("nav a[href], header a[href], [role=navigation] a[href], [class*=menu i] a[href]")
        en_menu = {id(a) for a in menu}
        for a in soup.find_all("a", href=True):
            texto = limpiar_nombre(a.get_text(" ", strip=True))
            url = urljoin(sitio, a["href"]).split("#")[0].split("?")[0]
            if not texto or not _mismo_sitio(url, sitio):
                continue
            nombres.setdefault(tuple(_clave(url)), texto)
            ruta = urlparse(url).path
            if (ENLACE_CATEGORIA.search(ruta) and not NO_CATEGORIA.search(ruta)
                    and (id(a) in en_menu or not menu)
                    and not (PATRON_FUERTE.search(ruta) and not NO_PRODUCTO.search(ruta))):
                candidatas_menu.append(url)
    except Exception as e:  # noqa: BLE001
        log(f"No se pudo leer la portada: {e}")

    # 1) Sitemaps de categorías
    rp = d.robots(sitio)
    pendientes = list((rp.site_maps() if rp else None) or []) or [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    vistos, urls_cat = set(), []
    leidos = 0
    while pendientes and leidos < max_sitemaps:
        sm = pendientes.pop(0)
        if sm in vistos:
            continue
        vistos.add(sm)
        locs = _leer_sitemap(d, sm, sitio)
        leidos += 1
        hijos = [u for u in locs if re.search(r"\.xml(\.gz)?(\?.*)?$", u, re.I)]
        nombre_sm = re.sub(r"site-?map", "", sm.rsplit("/", 1)[-1], flags=re.I)
        if hijos and len(hijos) >= len(locs) / 2:
            pendientes += [h for h in hijos if SITEMAP_CATEGORIAS.search(re.sub(r"site-?map", "", h.rsplit("/", 1)[-1], flags=re.I))]
        elif SITEMAP_CATEGORIAS.search(nombre_sm):
            urls_cat += [u for u in locs if _mismo_sitio(u, sitio)]
    log(f"{len(urls_cat)} categorías en sitemaps, {len(candidatas_menu)} en el menú")

    # 2) Menú de la portada: solo si el sitemap no trae categorías (si no, añade ruido)
    fuentes = urls_cat or candidatas_menu
    vistos_nombre = set()
    for url in list(dict.fromkeys(fuentes)):
        nombre = nombres.get(tuple(_clave(url)))
        if not urls_cat:  # en menús, el mismo nombre suele repetirse con otra dirección
            if not nombre or nombre.lower() in vistos_nombre or NOMBRE_NO_CATEGORIA.search(nombre):
                continue
            vistos_nombre.add(nombre.lower())
        arbol.añadir(url, nombre)
    arbol.ordenar()
    return arbol


# ---------- Caché (las categorías cambian poco) ----------
_cache: dict[str, ArbolCategorias] = {}
_cerrojo = threading.Lock()
CADUCIDAD = 6 * 3600


def categorias_de(sitio: str, d: Descargador | None = None) -> ArbolCategorias:
    clave = urlparse(sitio).netloc.lower().removeprefix("www.")
    with _cerrojo:
        arbol = _cache.get(clave)
        if arbol and time.time() - arbol.creado < CADUCIDAD:
            return arbol
    propio = d is None
    d = d or Descargador(pausa=0.5)
    try:
        arbol = obtener_categorias(d, sitio)
    finally:
        if propio:
            d.cerrar()
    with _cerrojo:
        _cache[clave] = arbol
    return arbol
