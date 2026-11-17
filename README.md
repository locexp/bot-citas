# SuperBot: scraper de productos de supermercado

Descarga **título, descripción, precio e imágenes** de productos de webs de supermercado.
Se usa desde el **móvil** (app web) o desde la línea de comandos.

## 🌐 Web del proyecto (GitHub Pages)
**https://locexp.github.io/bot-citas/** tiene la presentación y las instrucciones, y en
**/app/** la interfaz para el móvil. GitHub Pages solo aloja páginas estáticas, así que
la app necesita un bot funcionando (en Termux, tu PC o un servidor): pulsa **⚙ Servidor** y
escribe su dirección. También vale `…/app/?servidor=http://localhost:8000`.

La web se publica sola con `.github/workflows/pages.yml` a partir de `pagina/` y `web/`.
Para activarla la primera vez: en GitHub, **Settings → Pages → Source: GitHub Actions**.

Por seguridad, el bot solo acepta peticiones de otras webs si vienen de `*.github.io` o de
`localhost` (se cambia con `BOT_ORIGENES`, por ejemplo `BOT_ORIGENES=https://mi-dominio.com`).

## 📱 Usarlo desde el móvil

### Opción A: en el propio móvil Android con Termux (recomendada)
El bot funciona con la conexión de tu móvil, así que es la opción que menos bloqueos recibe.

1. Instala **Termux** desde F-Droid (https://f-droid.org/packages/com.termux/).
2. En Termux:
   ```bash
   pkg install -y git
   git clone https://github.com/locexp/bot-citas.git
   cd bot-citas
   ./iniciar.sh
   ```
3. Abre el navegador del móvil en **http://localhost:8000**.
4. Menú del navegador → «Añadir a pantalla de inicio» para tenerla como una app.

Las siguientes veces solo hace falta `cd bot-citas && ./iniciar.sh`.

### Opción B: en tu PC y abrirla desde el móvil (Android o iPhone)
```bash
pip install -r requirements.txt
python app.py
```
La consola muestra una dirección del tipo `http://192.168.1.20:8000`. Ábrela en el móvil
conectado a la **misma wifi**.

### Opción C: en un servidor (Render, Railway, un VPS...)
Comando de inicio: `python app.py` (usa la variable `PORT`). Pon **`BOT_CLAVE`** para que
nadie más pueda usarla. Ojo: muchas tiendas (incluida Supermercados MAS) bloquean las IPs de
servidores con Cloudflare, así que las opciones A y B suelen funcionar mejor.

### Cómo se usa la app
- **Productos**: pega uno o varios enlaces de producto (uno por línea).
- **Categoría**: pega el enlace de una categoría, de las ofertas o de una búsqueda, y el bot
  descarga todos los productos que aparezcan en ella.
- Al terminar verás las fichas con foto, precio y descripción, y podrás descargar un
  **ZIP** (con las imágenes, el CSV y el JSON), el **CSV** (se abre en Excel) o el **JSON**.

| Variable de entorno | Qué hace |
|---|---|
| `BOT_CLAVE` | Pide esta clave para usar la app |
| `PORT` | Puerto (8000 por defecto) |
| `BOT_MAX_PRODUCTOS` | Límite de productos por búsqueda (40) |
| `BOT_DATOS` | Carpeta donde se guardan las descargas (se borran a las 6 h) |

## ✈️ Usarlo desde Telegram
Le mandas un enlace al bot y te contesta con la foto, el precio y la descripción de cada
producto, y al final un ZIP con imágenes, CSV y JSON. Funciona en cualquier móvil.

1. En Telegram, abre **@BotFather** → `/newbot` → elige nombre → copia el **token**.
2. Copia `.env.ejemplo` como `.env` y pega el token:
   ```bash
   cp .env.ejemplo .env
   nano .env
   ```
3. Arranca el bot (en Termux, en tu PC o en un servidor):
   ```bash
   ./iniciar.sh telegram
   ```
4. Escríbele `/yo` a tu bot, pon ese número en `TELEGRAM_USUARIOS` dentro de `.env` y
   reinicia. Así solo tú (y quien añadas) podréis usarlo.

**Comandos**
| Mensaje | Qué hace |
|---|---|
| un enlace (o varios) | Descarga esos productos |
| `/categoria <enlace> [máx]` | Todos los productos de una categoría, oferta o búsqueda (20 por defecto) |
| `/opciones` | Ver opciones · `/opciones imagenes no` · `/opciones navegador si` · `/opciones max_imagenes 3` |
| `/yo` | Tu id de usuario |
| `/ayuda` | Ayuda |

El bot tiene que estar en marcha en algún dispositivo para responder. Si lo dejas en el
móvil con Termux, activa «Acquire wakelock» en la notificación de Termux para que Android
no lo pare.

## 💻 Línea de comandos
```bash
python bot.py https://super.com/producto/leche https://super.com/producto/pan
python bot.py --categoria https://super.com/lacteos --max 30
python bot.py -f urls.txt                # una URL por línea
python bot.py --navegador URL            # webs con JavaScript (requiere Playwright)
```
El resultado se guarda en `salida/`: `productos.json`, `productos.csv` e `imagenes/`.

Opciones: `--config selectores.json` (selectores CSS propios por dominio, ver
`selectores.ejemplo.json`), `--patron REGEX`, `--sin-imagenes`, `--max-imagenes N`,
`--formato json|csv|ambos`, `--pausa S`.

## Cómo detecta los datos
1. Selectores CSS propios (`selectores.json`, si existe)
2. JSON-LD `schema.org/Product`
3. Microdatos `itemprop`
4. Metaetiquetas OpenGraph
5. Tiendas **Magento** (como supermercadosmas.com): precio `data-price-amount`, galería, enlaces `product-item-link`
6. Heurísticas (`h1`, clases `price`/`precio`, imágenes)

## Webs probadas (16/11/2026, desde un servidor en la nube)
| Supermercado | Resultado | Notas |
|---|---|---|
| Alcampo (compraonline.alcampo.es) | ✅ Funciona | Productos y categorías |
| Ahorramas (ahorramas.com) | ✅ Funciona | Productos y categorías |
| Lidl (lidl.es) | ✅ Funciona | Productos y categorías |
| Consum (tienda.consum.es) | ✅ Con «modo navegador» | Web con JavaScript; muchos productos no tienen descripción |
| Mercadona (tienda.mercadona.es) | ⛔ No | Su `robots.txt` no permite bots; el bot lo respeta |
| Supermercados MAS, Carrefour, Eroski | 🛡️ Bloqueado (Cloudflare) | Puede funcionar desde casa o el móvil |
| Dia, El Corte Inglés | 🛡️ Bloqueado (Akamai) | Puede funcionar desde casa o el móvil |

Las webs cambian a menudo: si alguna deja de funcionar, se puede ajustar con `selectores.json`.

**Modo navegador:** necesita `pip install playwright && playwright install chromium`
(en Termux no está disponible). Si ya tienes Chromium instalado, puedes indicar su ruta con
`BOT_CHROMIUM=/ruta/a/chromium`.

## Webs con protección anti-bots
Si una web responde con un reto de Cloudflare («Just a moment...») o un bloqueo de Akamai
(«Access Denied»), el bot lo detecta y lo
avisa en lugar de devolver datos vacíos. Normalmente se soluciona ejecutándolo desde una
conexión doméstica o móvil (opciones A y B). El bot no intenta saltarse esas protecciones.

## Uso responsable
Por defecto respeta `robots.txt` y espera 1,5 s entre peticiones. Revisa los términos de uso
de cada web y no la sobrecargues.

## Tests
```bash
pip install pytest && python -m pytest tests
```
