#!/usr/bin/env python3
"""Bot para descargar título, descripción, precio e imágenes de productos
de una web de supermercado (línea de comandos). Para usarlo desde el móvil: app.py.

Ejemplos:
  python bot.py https://tienda.com/producto/leche-entera-1l
  python bot.py --categoria https://tienda.com/lacteos --max 20
  python bot.py -f urls.txt --navegador --formato csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scraper.descargador import Descargador
from scraper.servicio import guardar, scrapear


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

    salida = Path(args.salida)
    d = Descargador(pausa=args.pausa, usar_navegador=args.navegador,
                    respetar_robots=not args.ignorar_robots)
    try:
        productos, errores = scrapear(
            urls, args.categoria, salida, max_por_categoria=args.max, patron=args.patron,
            descargar_imagenes=not args.sin_imagenes, max_imagenes=args.max_imagenes,
            config=args.config, descargador=d)
    finally:
        d.cerrar()
    guardar(productos, salida, args.formato)
    print(f"\n{len(productos)} productos guardados en {salida.resolve()}")
    if errores:
        print(f"{len(errores)} errores (ver arriba)")
    return 0 if productos else 1


if __name__ == "__main__":
    sys.exit(main())
