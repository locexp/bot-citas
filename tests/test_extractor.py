from scraper.extractor import extraer_producto, extraer_enlaces_productos, parsear_precio

JSONLD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
{"@type":"Product","name":"Leche entera 1L","description":"<p>Leche de vaca</p>",
"image":["/img/leche1.jpg","https://cdn.x.com/leche2.jpg"],
"offers":{"@type":"Offer","price":"1.05","priceCurrency":"EUR"}}]}
</script></head><body><h1>Otro</h1></body></html>"""

HEUR = """<html><head><meta property="og:image" content="https://cdn.x.com/a.png">
<meta name="description" content="Aceite de oliva virgen extra"></head>
<body><h1> Aceite  AOVE 1L </h1><span class="product-price">4,59 €</span></body></html>"""


def test_jsonld():
    p = extraer_producto(JSONLD, "https://super.es/p/leche")
    assert p.titulo == "Leche entera 1L"
    assert p.descripcion == "Leche de vaca"
    assert p.precio == 1.05 and p.moneda == "EUR"
    assert p.imagenes == ["https://super.es/img/leche1.jpg", "https://cdn.x.com/leche2.jpg"]


def test_heuristicas():
    p = extraer_producto(HEUR, "https://super.es/p/aceite")
    assert p.titulo == "Aceite AOVE 1L"
    assert p.precio == 4.59 and p.moneda == "EUR"
    assert p.descripcion.startswith("Aceite de oliva")
    assert p.imagenes == ["https://cdn.x.com/a.png"]


def test_selectores():
    html = '<div class="t">Pan</div><b class="pr">$ 1,234.50</b>'
    p = extraer_producto(html, "https://s.com/x", {"titulo": ".t", "precio": ".pr"})
    assert p.titulo == "Pan" and p.precio == 1234.5 and p.moneda == "USD"


def test_precios():
    assert parsear_precio("1.234,56 €") == 1234.56
    assert parsear_precio("2,99") == 2.99
    assert parsear_precio("$1,299") == 1299
    assert parsear_precio("1.299") == 1299
    assert parsear_precio(3) == 3.0


def test_enlaces():
    html = '<a href="/producto/leche">a</a><a href="/contacto">b</a><a href="/producto/leche#x">c</a>'
    assert extraer_enlaces_productos(html, "https://s.es/cat") == ["https://s.es/producto/leche"]


MAGENTO = """<html><body><h1 class="page-title"><span itemprop="name">Aceite Oliva 5L</span></h1>
<div class="product-info-main"><span data-price-type="finalPrice" data-price-amount="26.95">
<span class="price">26,95&nbsp;€</span></span></div>
<div class="product attribute description"><div class="value">Aceite de oliva suave</div></div>
<script type="text/x-magento-init">{"[data-gallery-role=gallery-placeholder]":{"mage/gallery/gallery":
{"data":[{"thumb":"https:\\/\\/s.es\\/t.jpg","img":"https:\\/\\/s.es\\/m\\/a.jpg","full":"https:\\/\\/s.es\\/m\\/a_full.jpg"}]}}}</script>
<a class="product-item-link" href="/aceite-otro.html">x</a></body></html>"""


def test_magento():
    p = extraer_producto(MAGENTO, "https://s.es/aceite.html")
    assert p.titulo == "Aceite Oliva 5L"
    assert p.precio == 26.95 and p.moneda == "EUR"
    assert p.descripcion == "Aceite de oliva suave"
    assert "https://s.es/m/a_full.jpg" in p.imagenes
    assert extraer_enlaces_productos(MAGENTO, "https://s.es/cat")[0] == "https://s.es/aceite-otro.html"


def test_bloqueo_cloudflare():
    import pytest
    from scraper.descargador import BloqueoAntiBot, comprobar_bloqueo
    with pytest.raises(BloqueoAntiBot):
        comprobar_bloqueo(403, "<title>Just a moment...</title>", "https://x.es")


def test_ficha_telegram():
    from telegram_bot import ficha
    t = ficha({"titulo": "Leche *Entera*_1L", "precio": 0.99, "moneda": "EUR",
               "descripcion": "x" * 900, "url": "https://s.es/leche_1l"})
    assert "0,99 €" in t and "\\*Entera\\*\\_1L" in t and "https://s.es/leche\\_1l" in t
    assert len(t) < 1000


def test_url_publica(monkeypatch):
    from scraper.servicio import url_publica
    monkeypatch.delenv("BOT_PERMITIR_LOCAL", raising=False)
    assert not url_publica("http://127.0.0.1/admin")
    assert not url_publica("file:///etc/passwd")


def test_imagenes_mismo_producto_distinto_tamano():
    html = """<script type="application/ld+json">{"@type":"Product","name":"A","image":[
      "https://s.es/images-v3/aa/bb/500x500.jpg","https://s.es/images-v3/aa/bb/300x300.jpg",
      "https://s.es/on/demandware.static/x/large/foto_1.jpg",
      "https://s.es/dw/image/v2/BF_PRD/on/demandware.static/x/large/foto_1.jpg?sw=140",
      "https://s.es/images-v3/aa/cc/500x500.jpg"]}</script>"""
    p = extraer_producto(html, "https://s.es/p/a")
    assert len(p.imagenes) == 3


def test_enlaces_fuertes_ganan_a_debiles():
    html = """<a href="/folletos.html">f</a><a href="/navidad.html">n</a>
    <a href="/almeja-japonesa-500g-52679.html">a</a><a href="/p/sarten/p100410108">s</a>
    <a href="https://otra.com/producto/x">x</a>"""
    assert extraer_enlaces_productos(html, "https://s.es/") == [
        "https://s.es/almeja-japonesa-500g-52679.html", "https://s.es/p/sarten/p100410108"]


def test_bloqueo_akamai():
    import pytest
    from scraper.descargador import BloqueoAntiBot, comprobar_bloqueo
    with pytest.raises(BloqueoAntiBot, match="Akamai"):
        comprobar_bloqueo(403, "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>", "https://x.es")


def test_descripcion_slug_descartada():
    html = """<meta property="og:description" content="mejillones-gallegos-escabeche">
    <h1>Mejillones</h1><span class="price">3,65 €</span>
    <div class="product-description">Mejillones gallegos en escabeche, pack de 3 latas de 69 g.</div>"""
    p = extraer_producto(html, "https://s.es/p/mejillones/1")
    assert p.descripcion.startswith("Mejillones gallegos en escabeche")


def test_descripcion_no_coge_promociones():
    html = """<h1>Activia</h1><div class="product-info-promotions__with-description">Ahora más barato
    Oferta desde 21/10/2026 hasta 18/11/2026 bla bla</div><div class="product-data__description"></div>"""
    assert extraer_producto(html, "https://s.es/p/a/1").descripcion is None
