# Bot scraper de supermercado

Descarga **título, descripción, precio e imágenes** de productos de webs de supermercado.

## Instalación
```bash
pip install -r requirements.txt
# Solo si la web carga los productos con JavaScript:
pip install playwright && playwright install chromium
```

## Uso
```bash
# Uno o varios productos
python bot.py https://super.com/producto/leche https://super.com/producto/pan

# Todos los productos de una categoría o búsqueda
python bot.py --categoria https://super.com/lacteos --max 30

# Lista de URLs en un fichero (una por línea)
python bot.py -f urls.txt

# Webs con JavaScript (Mercadona, Carrefour, etc. suelen necesitarlo)
python bot.py --navegador --categoria https://super.com/ofertas
```

Resultado en `salida/`:
- `productos.json` y `productos.csv` (se abre en Excel)
- `imagenes/<nombre-producto>/01_xxxx.jpg ...`

## Opciones útiles
| Opción | Qué hace |
|---|---|
| `--navegador` | Usa Chromium para páginas con JavaScript |
| `--config selectores.json` | Selectores CSS propios por dominio (ver `selectores.ejemplo.json`) |
| `--patron REGEX` | Cómo reconocer enlaces de producto en una categoría |
| `--sin-imagenes` / `--max-imagenes N` | Controla la descarga de imágenes |
| `--formato json\|csv\|ambos` | Formato de salida |
| `--pausa S` | Segundos entre peticiones (por defecto 1.5) |

## Cómo detecta los datos
1. Selectores CSS del `--config` (si los das)
2. JSON-LD `schema.org/Product` (lo usan la mayoría de tiendas)
3. Microdatos `itemprop`
4. Metaetiquetas OpenGraph
5. Heurísticas (`h1`, elementos con clase `price`/`precio`, imágenes)

## Uso responsable
Respeta `robots.txt` por defecto y espera entre peticiones. Revisa los términos de uso
de cada web antes de hacer scraping y no la sobrecargues.

## Tests
```bash
pip install pytest && python -m pytest tests
```
