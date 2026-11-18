"""Lógica común del bot, usada por la línea de comandos (bot.py) y la web (app.py)."""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import os
import socket
import zipfile
from pathlib import Path
import threading
from typing import Callable
from urllib.parse import urlparse

from .descargador import Descargador, slug
from .extractor import extraer_enlaces_productos, extraer_producto
from .rastreador import descubrir_productos, productos_de_categoria


def cargar_selectores(ruta: str | None, url: str) -> dict | None:
    if not ruta or not Path(ruta).exists():
        return None
    config = json.loads(Path(ruta).read_text(encoding="utf-8"))
    dominio = urlparse(url).netloc.removeprefix("www.")
    return config.get(dominio) or config.get("*")


def scrapear(urls: list[str], categorias: list[str], salida: Path, *, max_por_categoria=50,
             patron=None, descargar_imagenes=True, max_imagenes=5, config=None,
             descargador: Descargador | None = None,
             log: Callable[[str], None] = print,
             sitios: list[str] | None = None, max_escaneo: int = 300, filtro: str | None = None,
             parar: threading.Event | None = None) -> tuple[list[dict], list[str]]:
    """Devuelve (productos, errores). Guarda imágenes en salida/imagenes/.

    - urls: páginas de producto
    - categorias: páginas de categoría/búsqueda (se extraen sus productos)
    - sitios: webs completas que se escanean (sitemap o recorrido) hasta max_escaneo productos
    - parar: si se activa, se detiene y devuelve lo descargado hasta ese momento
    """
    d = descargador or Descargador()
    productos, errores = [], []
    urls = list(urls)
    detener = lambda: bool(parar and parar.is_set())  # noqa: E731
    try:
        for sitio in sitios or []:
            try:
                urls += descubrir_productos(d, sitio, max_escaneo, filtro, log, parar, patron)
            except Exception as e:  # noqa: BLE001
                errores.append(f"{sitio}: {e}")
                log(f" ! {e}")
        for cat in categorias:
            if detener():
                break
            log(f"Categoría: {cat}")
            try:
                enlaces = productos_de_categoria(d, cat, max_por_categoria, log, parar, patron)
            except Exception as e:
                errores.append(f"{cat}: {e}")
                log(f" ! {e}")
                continue
            log(f" -> {len(enlaces)} productos encontrados")
            if not enlaces:
                errores.append(f"{cat}: no se encontraron enlaces a productos")
            urls += enlaces

        urls = list(dict.fromkeys(urls))
        if sitios and not urls and not errores:
            errores.append("No se encontraron productos en la web" + (f" con el filtro «{filtro}»" if filtro else ""))
        fallos_seguidos = 0
        for i, url in enumerate(urls, 1):
            if detener():
                log(f"⏹ Parado tras {len(productos)} productos")
                errores.append(f"Parado a petición: {len(productos)} de {len(urls)} productos")
                break
            log(f"[{i}/{len(urls)}] {url}")
            try:
                html = d.html(url)
                fallos_seguidos = 0
            except Exception as e:
                errores.append(f"{url}: {e}")
                log(f"   ! {e}")
                fallos_seguidos += 1
                if fallos_seguidos >= 15:  # la web nos ha bloqueado o se ha caído
                    errores.append("Demasiados fallos seguidos: se detiene el escaneo")
                    break
                continue
            p = extraer_producto(html, url, cargar_selectores(config, url))
            p.imagenes = p.imagenes[:max_imagenes]
            precio = f"{p.precio} {p.moneda or ''}".strip() if p.precio is not None else "¿?"
            log(f"   {p.titulo or '(sin título)'} | {precio} | {len(p.imagenes)} imágenes")
            if descargar_imagenes:
                carpeta = salida / "imagenes" / f"{i:04d}-{slug(p.titulo or 'producto')}"
                for n, img in enumerate(p.imagenes, 1):
                    if ruta := d.imagen(img, carpeta, f"{n:02d}"):
                        p.imagenes_locales.append(str(Path(ruta).relative_to(salida)))
            productos.append(p.to_dict())
    finally:
        if descargador is None:
            d.cerrar()
    return productos, errores


def guardar(productos: list[dict], salida: Path, formato: str = "ambos"):
    salida.mkdir(parents=True, exist_ok=True)
    if formato in ("json", "ambos"):
        (salida / "productos.json").write_text(
            json.dumps(productos, ensure_ascii=False, indent=2), encoding="utf-8")
    if formato in ("csv", "ambos") and productos:
        with open(salida / "productos.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(productos[0].keys()))
            w.writeheader()
            for p in productos:
                w.writerow({k: " | ".join(v) if isinstance(v, list) else v for k, v in p.items()})


def comprimir(carpeta: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in carpeta.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(carpeta))
    return buf.getvalue()


def comprimir_a_fichero(carpeta: Path, destino: Path) -> Path:
    """Como comprimir() pero en disco (para escaneos grandes: no llena la memoria)."""
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(carpeta.rglob("*")):
            if f.is_file() and f != destino:
                z.write(f, f.relative_to(carpeta))
    return destino


def comprimir_por_partes(carpeta: Path, destino: Path, limite: int = 45 * 1024 * 1024) -> list[Path]:
    """ZIPs de como mucho `limite` bytes (Telegram admite 50 MB por fichero).
    productos.csv y productos.json van en la primera parte."""
    ficheros = sorted((f for f in carpeta.rglob("*") if f.is_file()),
                      key=lambda f: (f.name not in ("productos.csv", "productos.json"), str(f)))
    partes, actual, tam, z = [], None, 0, None
    for f in ficheros:
        peso = f.stat().st_size
        if z is None or (tam + peso > limite and tam > 0):
            if z:
                z.close()
            actual = destino.with_name(f"{destino.stem}-parte{len(partes) + 1}.zip")
            partes.append(actual)
            z, tam = zipfile.ZipFile(actual, "w", zipfile.ZIP_DEFLATED), 0
        z.write(f, f.relative_to(carpeta))
        tam += peso
    if z:
        z.close()
    return partes


def url_publica(url: str) -> bool:
    """Evita que se use el bot para acceder a la red interna del servidor."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        try:
            infos = socket.getaddrinfo(u.hostname, None)
        except socket.gaierror:
            return True  # sin DNS local (p. ej. tras un proxy): la petición decidirá
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return os.environ.get("BOT_PERMITIR_LOCAL") == "1"
        return True
    except ValueError:
        return False
