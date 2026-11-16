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
