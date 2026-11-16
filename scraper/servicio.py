"""Lógica común del bot, usada por la línea de comandos (bot.py) y la web (app.py)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .descargador import Descargador, slug
from .extractor import extraer_enlaces_productos, extraer_producto


def cargar_selectores(ruta: str | None, url: str) -> dict | None:
    if not ruta or not Path(ruta).exists():
        return None
    config = json.loads(Path(ruta).read_text(encoding="utf-8"))
    dominio = urlparse(url).netloc.removeprefix("www.")
    return config.get(dominio) or config.get("*")


def scrapear(urls: list[str], categorias: list[str], salida: Path, *, max_por_categoria=50,
             patron=None, descargar_imagenes=True, max_imagenes=5, config=None,
             descargador: Descargador | None = None,
             log: Callable[[str], None] = print) -> tuple[list[dict], list[str]]:
    """Devuelve (productos, errores). Guarda imágenes en salida/imagenes/."""
    d = descargador or Descargador()
    productos, errores = [], []
    urls = list(urls)
    try:
        for cat in categorias:
            log(f"Categoría: {cat}")
            try:
                enlaces = extraer_enlaces_productos(d.html(cat), cat, patron)[:max_por_categoria]
            except Exception as e:
                errores.append(f"{cat}: {e}")
                log(f" ! {e}")
                continue
            log(f" -> {len(enlaces)} productos encontrados")
            if not enlaces:
                errores.append(f"{cat}: no se encontraron enlaces a productos")
            urls += enlaces

        urls = list(dict.fromkeys(urls))
        for i, url in enumerate(urls, 1):
            log(f"[{i}/{len(urls)}] {url}")
            try:
                html = d.html(url)
            except Exception as e:
                errores.append(f"{url}: {e}")
                log(f"   ! {e}")
                continue
            p = extraer_producto(html, url, cargar_selectores(config, url))
            p.imagenes = p.imagenes[:max_imagenes]
            precio = f"{p.precio} {p.moneda or ''}".strip() if p.precio is not None else "¿?"
            log(f"   {p.titulo or '(sin título)'} | {precio} | {len(p.imagenes)} imágenes")
            if descargar_imagenes:
                carpeta = salida / "imagenes" / f"{i:03d}-{slug(p.titulo or 'producto')}"
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
