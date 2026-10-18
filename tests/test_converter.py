import pytest
from PIL import Image

from app import converter


def test_pdf_pages_and_blocks(samples, tmp_path):
    kind, pages = converter.convert(samples / "sample_briefing.pdf", tmp_path)
    assert kind == "pdf"
    assert len(pages) == 2
    first = pages[0]
    assert (tmp_path / first.image).exists()
    assert max(first.width, first.height) == pytest.approx(converter.MAX_EDGE, abs=2)
    texts = [b.text for b in first.blocks]
    assert any("스마트시티" in t for t in texts)
    for b in first.blocks:
        assert 0 <= b.x <= 1 and 0 <= b.y <= 1 and 0 < b.w <= 1 and 0 < b.h <= 1
    footnote = next(b for b in first.blocks if "별첨" in b.text)
    assert footnote.font_size == pytest.approx(8, abs=0.5)


def test_image_downscaled(tmp_path):
    src = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 2000), "white").save(src)
    kind, pages = converter.convert(src, tmp_path / "out")
    assert kind == "image"
    assert (pages[0].width, pages[0].height) == (1568, 784)
    assert pages[0].blocks == []


@pytest.mark.skipif(not converter.soffice_path(), reason="LibreOffice 없음")
def test_pptx_via_libreoffice(samples, tmp_path):
    src = samples / "sample_briefing.pptx"
    if not src.exists():
        pytest.skip("python-pptx 없음")
    kind, pages = converter.convert(src, tmp_path)
    assert kind == "office"
    assert len(pages) == 2
    assert any("추진" in b.text for b in pages[1].blocks)


def test_html_screenshot_and_dom_blocks(samples, tmp_path):
    pytest.importorskip("playwright")
    kind, pages = converter.convert(samples / "sample_notice.html", tmp_path)
    assert kind == "html"
    assert pages
    texts = [b.text for p in pages for b in p.blocks]
    assert any("마이데이터" in t for t in texts)
    tiny = next(b for p in pages for b in p.blocks if "동의 철회" in b.text)
    assert tiny.font_size == pytest.approx(9, abs=0.5)


def test_unsupported_extension(tmp_path):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"0")
    with pytest.raises(converter.ConversionError):
        converter.convert(bad, tmp_path / "out")
