from app.normalize import normalize_country, normalize_licence, normalize_name, normalize_phone


def test_strips_titles_punctuation_and_case() -> None:
    assert normalize_name("Dr. Amina Wanjiku!") == normalize_name("amina wanjiku")


def test_name_order_is_equivalent() -> None:
    assert normalize_name("Wanjiku Amina") == normalize_name("Amina Wanjiku")


def test_licence_strips_spaces_and_hyphens() -> None:
    assert normalize_licence("kefwa-sim-001") == "KEFWASIM001"
    assert normalize_licence("KEFWA SIM 001") == "KEFWASIM001"


def test_phone_kenya_local_to_country_code() -> None:
    assert normalize_phone("0700000001") == "254700000001"
    assert normalize_phone("+254 700 000 001") == "254700000001"


def test_country_normalises_case() -> None:
    assert normalize_country("  Kenya ") == "kenya"
