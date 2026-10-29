from app import regions_signature


def region(rect):
    return {"processing_basis": "boundary_based", "source_method": "flood", "bubble_rect": rect}


def test_signature_ignores_order_and_changes_with_rect():
    a = {"regions": [region([0, 0, 10, 10]), region([20, 20, 5, 5])]}
    b = {"regions": [region([20, 20, 5, 5]), region([0, 0, 10, 10])]}
    c = {"regions": [region([0, 0, 11, 10]), region([20, 20, 5, 5])]}
    assert regions_signature(a) == regions_signature(b)
    assert regions_signature(a) != regions_signature(c)


def test_signature_none_without_regions():
    assert regions_signature({}) is None
