"""Extracción de título, descripción, precio e imágenes de una página de producto.

Estrategias, en orden de prioridad:
1. Selectores CSS definidos por el usuario (config por dominio).
2. JSON-LD schema.org/Product (lo usan la mayoría de supermercados online).
3. Microdatos (itemprop="name", "price", ...).
4. Metaetiquetas OpenGraph / product:price.
5. Heurísticas genéricas (h1, clases con "price", imágenes grandes).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin

from bs4 import BeautifulSoup


@dataclass
class Producto:
    url: str
    titulo: str | None = None
    descripcion: str | None = None
    precio: float | None = None
    moneda: str | None = None
    precio_texto: str | None = None
    imagenes: list[str] = field(default_factory=list)
    imagenes_locales: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


PRECIO_RE = re.compile(r"(\d(?:[\d.,]|\s(?=\d{3}\b))*\d|\d)")
MONEDAS = {"€": "EUR", "$": "USD", "£": "GBP", "S/": "PEN", "R$": "BRL"}


def parsear_precio(texto) -> float | None:
    """Convierte '1.234,56 €', '$ 1,234.56', '2,99' o 2.99 en float."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    m = PRECIO_RE.search(str(texto).replace("\xa0", " "))
    if not m:
        return None
    num = m.group(1).replace(" ", "")
    if "," in num and "." in num:
        # El último separador es el decimal
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        entero, _, dec = num.rpartition(",")
        num = f"{entero.replace(',', '')}.{dec}" if len(dec) <= 2 else num.replace(",", "")
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.split(".")[1]) == 3):
        num = num.replace(".", "")  # 1.234 -> miles
    try:
        return float(num)
    except ValueError:
        return None


def detectar_moneda(texto: str | None) -> str | None:
    if not texto:
        return None
    for simbolo, codigo in sorted(MONEDAS.items(), key=lambda x: -len(x[0])):
        if simbolo in texto:
            return codigo
    m = re.search(r"\b(EUR|USD|MXN|ARS|CLP|COP|PEN|GBP|BRL)\b", texto)
    return m.group(1) if m else None


def _limpiar(texto: str | None) -> str | None:
    if not texto:
        return None
    texto = BeautifulSoup(texto, "lxml").get_text(" ") if "<" in texto else texto
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


# ---------- JSON-LD ----------
def _iterar_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pila = [data]
        while pila:
            nodo = pila.pop()
            if isinstance(nodo, list):
                pila.extend(nodo)
            elif isinstance(nodo, dict):
                yield nodo
                if "@graph" in nodo:
                    pila.append(nodo["@graph"])


def _es_producto(nodo: dict) -> bool:
    t = nodo.get("@type")
    tipos = t if isinstance(t, list) else [t]
    return any(str(x).lower() in ("product", "productgroup") for x in tipos)


def _imagenes_de(valor) -> list[str]:
    if not valor:
        return []
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, dict):
        return [valor.get("url") or valor.get("contentUrl")] if (valor.get("url") or valor.get("contentUrl")) else []
    if isinstance(valor, list):
        out = []
        for v in valor:
            out.extend(_imagenes_de(v))
        return out
    return []


def _desde_jsonld(soup, p: Producto):
    for nodo in _iterar_jsonld(soup):
        if not _es_producto(nodo):
            continue
        p.titulo = p.titulo or _limpiar(nodo.get("name"))
        p.descripcion = p.descripcion or _limpiar(nodo.get("description"))
        p.imagenes += _imagenes_de(nodo.get("image"))
        ofertas = nodo.get("offers")
        if not ofertas and isinstance(nodo.get("hasVariant"), list) and nodo["hasVariant"]:
            ofertas = nodo["hasVariant"][0].get("offers")
        if isinstance(ofertas, list):
            ofertas = ofertas[0] if ofertas else None
        if isinstance(ofertas, dict):
            precio = ofertas.get("price") or ofertas.get("lowPrice")
            if precio is None and isinstance(ofertas.get("priceSpecification"), dict):
                precio = ofertas["priceSpecification"].get("price")
            if p.precio is None and precio is not None:
                p.precio = parsear_precio(precio)
                p.precio_texto = str(precio)
            p.moneda = p.moneda or ofertas.get("priceCurrency")
        return


# ---------- Microdatos / OpenGraph ----------
def _desde_microdatos(soup, p: Producto):
    def prop(nombre):
        el = soup.find(attrs={"itemprop": nombre})
        if not el:
            return None
        return el.get("content") or el.get("src") or el.get("href") or el.get_text(" ")

    p.titulo = p.titulo or _limpiar(prop("name"))
    p.descripcion = p.descripcion or _limpiar(prop("description"))
    if p.precio is None and (txt := prop("price")):
        p.precio, p.precio_texto = parsear_precio(txt), _limpiar(txt)
    p.moneda = p.moneda or _limpiar(prop("priceCurrency"))
    for el in soup.find_all(attrs={"itemprop": "image"}):
        if src := (el.get("content") or el.get("src") or el.get("href")):
            p.imagenes.append(src)


def _meta(soup, *nombres):
    for n in nombres:
        el = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if el and el.get("content"):
            return el["content"]
    return None


def _desde_meta(soup, p: Producto):
    p.titulo = p.titulo or _limpiar(_meta(soup, "og:title", "twitter:title"))
    p.descripcion = p.descripcion or _limpiar(_meta(soup, "og:description", "description", "twitter:description"))
    if p.precio is None and (txt := _meta(soup, "product:price:amount", "og:price:amount")):
        p.precio, p.precio_texto = parsear_precio(txt), txt
    p.moneda = p.moneda or _meta(soup, "product:price:currency", "og:price:currency")
    for el in soup.find_all("meta", attrs={"property": "og:image"}):
        if el.get("content"):
            p.imagenes.append(el["content"])


# ---------- Heurísticas ----------
def _desde_heuristicas(soup, p: Producto):
    if not p.titulo and (h1 := soup.find("h1")):
        p.titulo = _limpiar(h1.get_text(" "))
    if p.precio is None:
        for el in soup.select('[class*="price" i], [id*="price" i], [class*="precio" i], [data-testid*="price" i]'):
            txt = el.get_text(" ", strip=True)
            if (valor := parsear_precio(txt)) is not None and len(txt) < 40:
                p.precio, p.precio_texto = valor, txt
                break
    if not p.imagenes:
        for img in soup.select("main img, [class*='product' i] img, img"):
            src = img.get("data-src") or img.get("data-zoom-image") or img.get("src")
            if src and not src.startswith("data:") and not re.search(r"logo|icon|sprite|placeholder", src, re.I):
                p.imagenes.append(src)
            if len(p.imagenes) >= 5:
                break


# ---------- Selectores personalizados ----------
def _desde_selectores(soup, p: Producto, sel: dict):
    def texto(css):
        el = soup.select_one(css) if css else None
        return _limpiar(el.get_text(" ")) if el else None

    p.titulo = texto(sel.get("titulo")) or p.titulo
    p.descripcion = texto(sel.get("descripcion")) or p.descripcion
    if (txt := texto(sel.get("precio"))):
        p.precio, p.precio_texto = parsear_precio(txt), txt
    if css := sel.get("imagenes"):
        for img in soup.select(css):
            src = img.get("data-src") or img.get("src") or img.get("href")
            if src:
                p.imagenes.append(src)


def extraer_producto(html: str, url: str, selectores: dict | None = None) -> Producto:
    soup = BeautifulSoup(html, "lxml")
    p = Producto(url=url)
    if selectores:
        _desde_selectores(soup, p, selectores)
    _desde_jsonld(soup, p)
    _desde_microdatos(soup, p)
    _desde_meta(soup, p)
    _desde_heuristicas(soup, p)

    p.moneda = p.moneda or detectar_moneda(p.precio_texto)
    # Normalizar URLs de imágenes y quitar duplicados
    vistas, imgs = set(), []
    for src in p.imagenes:
        abs_src = urljoin(url, src.strip())
        if abs_src.startswith("http") and abs_src not in vistas:
            vistas.add(abs_src)
            imgs.append(abs_src)
    p.imagenes = imgs
    return p


def extraer_enlaces_productos(html: str, url: str, patron: str | None = None) -> list[str]:
    """En una página de categoría/búsqueda, devuelve los enlaces a productos."""
    soup = BeautifulSoup(html, "lxml")
    regex = re.compile(patron or r"/(p|producto|product|products|prod|item|articulo)[/-]|/p$|\.html$|-p\d+|/\d{5,}", re.I)
    enlaces, vistos = [], set()
    for nodo in _iterar_jsonld(soup):  # ItemList en JSON-LD
        if str(nodo.get("@type")).lower() == "itemlist":
            for it in nodo.get("itemListElement", []):
                if not isinstance(it, dict):
                    continue
                item = it.get("item")
                u = it.get("url") or (item.get("url") if isinstance(item, dict) else item)
                if u:
                    enlaces.append(urljoin(url, u))
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        if regex.search(href):
            enlaces.append(href)
    return [u for u in enlaces if not (u in vistos or vistos.add(u))]
