"""Geography Foundation (Issue #271, trips-and-tourist-profile.md §9):
the migration's Country and RU Region seeds and the catalog value
normalization."""

import hashlib
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest

from app.trips.geography import (
    InvalidGeographyValueError,
    RegionCountryMismatchError,
    _country_code,
    _optional_text,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2c28e7b34462_create_countries_regions_and_trip_.py"
)


def _migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("geography_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed() -> tuple[tuple[str, str], ...]:
    return _migration()._COUNTRIES


# The canonical RU administrative-subject seed as fixed by the PO
# (Constitution of the Russian Federation, Article 65, Part 1), in order.
_EXPECTED_RU_SUBJECTS = (
    "Республика Адыгея (Адыгея)",
    "Республика Алтай",
    "Республика Башкортостан",
    "Республика Бурятия",
    "Республика Дагестан",
    "Донецкая Народная Республика",
    "Республика Ингушетия",
    "Кабардино-Балкарская Республика",
    "Республика Калмыкия",
    "Карачаево-Черкесская Республика",
    "Республика Карелия",
    "Республика Коми",
    "Республика Крым",
    "Луганская Народная Республика",
    "Республика Марий Эл",
    "Республика Мордовия",
    "Республика Саха (Якутия)",
    "Республика Северная Осетия — Алания",
    "Республика Татарстан",
    "Республика Тыва",
    "Удмуртская Республика",
    "Республика Хакасия",
    "Чеченская Республика",
    "Чувашская Республика — Чувашия",
    "Алтайский край",
    "Забайкальский край",
    "Камчатский край",
    "Краснодарский край",
    "Красноярский край",
    "Пермский край",
    "Приморский край",
    "Ставропольский край",
    "Хабаровский край",
    "Амурская область",
    "Архангельская область",
    "Астраханская область",
    "Белгородская область",
    "Брянская область",
    "Владимирская область",
    "Волгоградская область",
    "Вологодская область",
    "Воронежская область",
    "Запорожская область",
    "Ивановская область",
    "Иркутская область",
    "Калининградская область",
    "Калужская область",
    "Кемеровская область — Кузбасс",
    "Кировская область",
    "Костромская область",
    "Курганская область",
    "Курская область",
    "Ленинградская область",
    "Липецкая область",
    "Магаданская область",
    "Московская область",
    "Мурманская область",
    "Нижегородская область",
    "Новгородская область",
    "Новосибирская область",
    "Омская область",
    "Оренбургская область",
    "Орловская область",
    "Пензенская область",
    "Псковская область",
    "Ростовская область",
    "Рязанская область",
    "Самарская область",
    "Саратовская область",
    "Сахалинская область",
    "Свердловская область",
    "Смоленская область",
    "Тамбовская область",
    "Тверская область",
    "Томская область",
    "Тульская область",
    "Тюменская область",
    "Ульяновская область",
    "Челябинская область",
    "Ярославская область",
    "Херсонская область",
    "город федерального значения Москва",
    "город федерального значения Санкт-Петербург",
    "город федерального значения Севастополь",
    "Еврейская автономная область",
    "Ненецкий автономный округ",
    "Ханты-Мансийский автономный округ — Югра",
    "Чукотский автономный округ",
    "Ямало-Ненецкий автономный округ",
)


def test_seed_is_the_iso_3166_1_alpha2_set_one_to_one() -> None:
    seed = _seed()
    codes = [code for code, _name in seed]
    assert len(seed) == 249
    assert len(set(codes)) == 249
    assert all(re.fullmatch(r"[A-Z]{2}", code) for code in codes)
    assert codes == sorted(codes)


def test_seed_names_are_russian() -> None:
    names = dict(_seed())
    assert all(re.search(r"[А-Яа-яЁё]", name) for name in names.values())
    assert (names["RU"], names["HR"], names["DE"]) == (
        "Россия",
        "Хорватия",
        "Германия",
    )


def test_seed_contains_no_region_or_non_iso_entries() -> None:
    codes = {code for code, _name in _seed()}
    # XK (Kosovo) and EU are user-assigned / reserved, not ISO 3166-1 entries.
    assert not {"XK", "EU", "UK", "SU"} & codes


@pytest.mark.parametrize(("raw", "code"), [("ru", "RU"), (" hr ", "HR"), ("De", "DE")])
def test_country_code_is_normalized(raw: str, code: str) -> None:
    assert _country_code(raw) == code


@pytest.mark.parametrize("raw", ["", " ", "R", "RUS", "1A", "Р1", "R-"])
def test_country_code_must_be_iso_alpha2(raw: str) -> None:
    with pytest.raises(InvalidGeographyValueError):
        _country_code(raw)


@pytest.mark.parametrize(("raw", "value"), [(None, None), ("", None), ("  ", None), (" x ", "x")])
def test_provenance_text_is_optional(raw: str | None, value: str | None) -> None:
    assert _optional_text(raw) == value


def test_region_country_mismatch_messages() -> None:
    import uuid

    region_id, country_id = uuid.uuid4(), uuid.uuid4()
    assert "requires the Trip's country" in str(RegionCountryMismatchError(region_id, None))
    assert "does not belong" in str(RegionCountryMismatchError(region_id, country_id))


# --- RU administrative-subject seed -----------------------------------------------


def test_ru_seed_is_exactly_the_89_canonical_subjects_in_order() -> None:
    subjects = _migration()._RU_SUBJECTS
    assert len(subjects) == 89
    assert tuple(name for _kind, _code, name in subjects) == _EXPECTED_RU_SUBJECTS


def test_ru_seed_structure_matches_article_65() -> None:
    kinds = Counter(kind for kind, _code, _name in _migration()._RU_SUBJECTS)
    assert kinds == {
        "republic": 24,
        "krai": 9,
        "oblast": 48,
        "federal_city": 3,
        "autonomous_oblast": 1,
        "autonomous_okrug": 4,
    }


def test_ru_seed_names_and_internal_codes_are_unique_ascii_slugs() -> None:
    subjects = _migration()._RU_SUBJECTS
    codes = [code for _kind, code, _name in subjects]
    names = [name for _kind, _code, name in subjects]
    assert len(set(codes)) == 89
    assert len(set(names)) == 89
    assert all(re.fullmatch(r"[A-Z][A-Z_]*[A-Z]", code) for code in codes)
    assert {"ADYGEA", "ALTAY_REPUBLIC", "BASHKORTOSTAN", "MOSCOW", "MOSCOW_OBLAST"} <= set(codes)
    assert {"ST_PETERSBURG", "SEVASTOPOL"} <= set(codes)


def test_ru_seed_snapshot_hash_is_pinned() -> None:
    module = _migration()
    snapshot = "".join(f"{name}\n" for _kind, _code, name in module._RU_SUBJECTS)
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    assert (
        digest
        == module._RU_SNAPSHOT_SHA256
        == ("07be4af5094d98aed298e08cf769cb5144bf33fe4bf3b1d6b723658228240e03")
    )


def test_ru_seed_has_no_baikonur_or_non_administrative_entries() -> None:
    module = _migration()
    names = " ".join(name for _kind, _code, name in module._RU_SUBJECTS).lower()
    assert "байконур" not in names
    assert "фстр" not in names
    assert module._RU_SOURCE_TYPE == "CONSTITUTION_RF_ARTICLE_65"
    assert module._RU_SOURCE_REFERENCE == (
        "Constitution of the Russian Federation, Article 65, Part 1; canonical TourCRM RU "
        "administrative-subject seed fixed by PO on 2026-10-05"
    )
