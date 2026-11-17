# SuperBot: scraper de productos de supermercado

Descarga **título, descripción, precio e imágenes** de productos de webs de supermercado.
Se usa desde el **móvil** (app web) o desde la línea de comandos.

## 🌐 Web del proyecto (GitHub Pages)
**https://locexp.github.io/bot-citas/** tiene la presentación y las instrucciones, y en
**/app/** la interfaz para el móvil. GitHub Pages solo aloja páginas estáticas, así que
la app necesita un bot funcionando (en Termux, tu PC o un servidor): pulsa **⚙ Servidor** y
escribe su dirección. También vale `…/app/?servidor=http://localhost:8000`.

La web está en la carpeta `docs/` (`docs/index.html` es la presentación y `docs/app/` la
app, que es la misma que sirve `app.py`). Para activarla la primera vez: en GitHub,
**Settings → Pages → Source: Deploy from a branch → rama `ccr-962fb0fd-stdhyu`** y carpeta `/docs`
(o `/ (root)`: el `index.html` de la raíz redirige a `docs/`).
Después, cada cambio que se suba a `docs/` se publica solo.

Por seguridad, el bot solo acepta peticiones de otras webs si vienen de `*.github.io` o de
`localhost` (se cambia con `BOT_ORIGENES`, por ejemplo `BOT_ORIGENES=https://mi-dominio.com`).

## ☁️ Servidor gratuito en Render (un botón)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/locexp/bot-citas)

1. Pulsa el botón y entra en Render con tu cuenta de GitHub (plan **Free**, sin tarjeta).
2. Render pregunta por `TELEGRAM_TOKEN` y `TELEGRAM_USUARIOS`: rellénalos si quieres el bot de
   Telegram, o déjalos vacíos. Pulsa **Apply / Deploy** y espera unos minutos.
3. Copia la dirección del servicio (algo como `https://superbot-xxxx.onrender.com`) y la clave
   que Render ha generado (servicio → **Environment** → `BOT_CLAVE`).
4. Abre la app (https://locexp.github.io/bot-citas/docs/app/ o directamente la dirección de
   Render), pulsa **⚙ Servidor**, pega la dirección y luego escribe la clave.

Si pusiste `TELEGRAM_TOKEN`, el bot de Telegram funciona en el mismo servidor (modo webhook):
no hace falta tener nada encendido en el móvil ni en el PC.

**Limitaciones del plan gratuito:** el servidor se «duerme» tras 15 minutos sin uso y tarda
alrededor de un minuto en despertar con la siguiente petición (o el siguiente mensaje de
Telegram). No incluye el «modo navegador». Y algunas tiendas bloquean los servidores en la
nube (ver la tabla de webs probadas). Alcampo, Ahorramas y Lidl funcionaron desde la nube.

También hay un `Dockerfile` para otros servicios gratuitos basados en Docker (Koyeb,
Hugging Face Spaces…).

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
| `/web <enlace> [cantidad] [filtro]` | **Escanea la web entera** y descarga sus productos (200 por defecto; `todos` = hasta 5000). Ej.: `/web https://www.ahorramas.com todos leche` |
| `/parar` | Detiene el escaneo y envía lo descargado hasta ese momento |
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

## Escanear una web entera
El bot busca los productos en el **sitemap** de la web (el índice que las tiendas publican
para los buscadores, indicado en su `robots.txt`). Si no hay, recorre la web por categorías
y páginas. Respeta el `Crawl-delay` que pida cada web.

| Web | Productos en su sitemap (nov. 2026) |
|---|---|
| Alcampo | ~96.000 |
| Lidl | ~7.500 |
| Ahorramas | ~4.500 |

Cada producto tarda unos 2 segundos (pausa de cortesía incluida): **1.000 productos ≈ 35 min**.
Por eso conviene usar la cantidad y el filtro. Con más de 10 productos, en Telegram se envía un
resumen y las tablas (CSV y JSON, con los enlaces de las fotos). Las imágenes de los escaneos
no se descargan salvo que lo pidas (`/opciones imagenes_web si`), y entonces van en ZIPs de
menos de 50 MB. En Render, el servidor no se duerme mientras haya un escaneo en marcha.
Si el servidor se reinicia (por ejemplo, al desplegar una versión nueva), el escaneo en
curso se pierde.

Variables: `BOT_MAX_ESCANEO` (máximo por escaneo, 5000), `BOT_LIMITE_ESCANEO` (segundos, 8 h).

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
