#!/usr/bin/env python3
"""Bot para descargar título, descripción, precio e imágenes de productos
de una web de supermercado.

Ejemplos:
  python bot.py https://tienda.com/producto/leche-entera-1l
  python bot.py --categoria https://tienda.com/lacteos --max 20
  python bot.py -f urls.txt --navegador --formato csv
  python bot.py URL --config selectores.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from scraper.descargador import Descargador, slug
from scraper.extractor import extraer_enlaces_productos, extraer_producto


def cargar_selectores(ruta: str | None, url: str) -> dict | None:
    if not ruta:
        return None
    config = json.loads(Path(ruta).read_text(encoding="utf-8"))
    dominio = urlparse(url).netloc.removeprefix("www.")
    return config.get(dominio) or config.get("*")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scraper de productos de supermercado")
    ap.add_argument("urls", nargs="*", help="URLs de páginas de producto")
    ap.add_argument("-f", "--fichero", help="Fichero de texto con una URL por línea")
    ap.add_argument("-c", "--categoria", action="append", default=[],
                    help="URL de categoría/búsqueda: se extraen sus productos")
    ap.add_argument("--patron", help="Regex para reconocer enlaces de producto en categorías")
    ap.add_argument("--max", type=int, default=50, help="Máx. productos por categoría (50)")
    ap.add_argument("-o", "--salida", default="salida", help="Carpeta de salida (salida/)")
    ap.add_argument("--formato", choices=["json", "csv", "ambos"], default="ambos")
    ap.add_argument("--sin-imagenes", action="store_true", help="No descargar imágenes")
    ap.add_argument("--max-imagenes", type=int, default=5, help="Imágenes por producto (5)")
    ap.add_argument("--navegador", action="store_true",
                    help="Usar Chromium (Playwright) para webs con JavaScript")
    ap.add_argument("--config", help="JSON con selectores CSS por dominio")
    ap.add_argument("--pausa", type=float, default=1.5, help="Segundos entre peticiones (1.5)")
    ap.add_argument("--ignorar-robots", action="store_true", help="No consultar robots.txt")
    args = ap.parse_args(argv)

    urls = list(args.urls)
    if args.fichero:
        urls += [l.strip() for l in Path(args.fichero).read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
    if not urls and not args.categoria:
        ap.error("indica al menos una URL, --fichero o --categoria")

    d = Descargador(pausa=args.pausa, usar_navegador=args.navegador,
                    respetar_robots=not args.ignorar_robots)
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    productos = []

    try:
        for cat in args.categoria:
            print(f"Categoría: {cat}")
            try:
                enlaces = extraer_enlaces_productos(d.html(cat), cat, args.patron)[: args.max]
            except Exception as e:
                print(f" ! Error leyendo categoría: {e}")
                continue
            print(f" -> {len(enlaces)} productos encontrados")
            urls += enlaces

        for i, url in enumerate(dict.fromkeys(urls), 1):
            print(f"[{i}/{len(urls)}] {url}")
            try:
                html = d.html(url)
            except Exception as e:
                print(f"   ! Error: {e}")
                continue
            p = extraer_producto(html, url, cargar_selectores(args.config, url))
            p.imagenes = p.imagenes[: args.max_imagenes]
            precio = f"{p.precio} {p.moneda or ''}".strip() if p.precio is not None else "¿?"
            print(f"   {p.titulo or '(sin título)'} | {precio} | {len(p.imagenes)} imágenes")
            if not args.sin_imagenes:
                carpeta = salida / "imagenes" / slug(p.titulo or str(i))
                for n, img in enumerate(p.imagenes, 1):
                    if ruta := d.imagen(img, carpeta, f"{n:02d}"):
                        p.imagenes_locales.append(ruta)
            productos.append(p.to_dict())
    finally:
        d.cerrar()

    if args.formato in ("json", "ambos"):
        (salida / "productos.json").write_text(
            json.dumps(productos, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.formato in ("csv", "ambos") and productos:
        with open(salida / "productos.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(productos[0].keys()))
            w.writeheader()
            for p in productos:
                w.writerow({k: " | ".join(v) if isinstance(v, list) else v for k, v in p.items()})
    print(f"\n{len(productos)} productos guardados en {salida.resolve()}")
    return 0 if productos else 1


if __name__ == "__main__":
    sys.exit(main())
