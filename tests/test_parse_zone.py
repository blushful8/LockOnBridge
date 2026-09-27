from lockon_bridge.layout_ocr import crop_parse_zone
from lockon_bridge.roi_calib import default_parse_zone, load_parse_zone
from PIL import Image


def test_default_parse_zone_is_padded_table() -> None:
    zone = default_parse_zone()
    assert zone.left < 0.25
    assert zone.right > 0.50
    assert zone.bottom - zone.top > 0.40


def test_crop_uses_one_fractional_zone() -> None:
    zone = load_parse_zone()
    image = Image.new("RGB", (1000, 600), (10, 10, 10))
    crop = crop_parse_zone(image)
    assert crop.width == int(1000 * zone.right) - int(1000 * zone.left)
    assert crop.height == int(600 * zone.bottom) - int(600 * zone.top)
    assert crop.width < image.width
