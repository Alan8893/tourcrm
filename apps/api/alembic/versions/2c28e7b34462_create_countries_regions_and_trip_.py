"""create countries regions and trip geography

Revision ID: 2c28e7b34462
Revises: 2a3f572d4f32
Create Date: 2026-10-05 12:47:20.287608

Issue #271 (Tourism Facts v2 — Geography Foundation), canonical source
docs/04-modules/trips-and-tourist-profile.md §9 (Issue #258, PR #270):

- creates the `countries` catalog: `code` = ISO 3166-1 alpha-2 (unique,
  CHECK `^[A-Z]{2}$`), `name` = canonical Russian display name, `active`,
  provenance `source_type`/`source_reference`;
- creates the `regions` catalog (seeded with the 89 subjects of the
  Russian Federation, see below): exactly one Country (FK RESTRICT),
  `code` unique within its Country, `name`, `semantic_type` (CHECK: only
  the approved `administrative_subject`), `active`, provenance;
- `first_used_at` on both catalogs marks the first time a Trip referenced
  the entry; from then on its semantic fields (Country `code`/`name`,
  Region `code`/`name`/`country_id`) are immutable by ordinary editing
  (§9). `active` and provenance stay editable;
- adds nullable `trips.country_id` (FK RESTRICT) and `trips.region_id`
  with the composite FK `(region_id, country_id) -> regions(id,
  country_id)` ON UPDATE/DELETE RESTRICT and CHECK `region_id IS NULL OR
  country_id IS NOT NULL`: a Trip's Region always belongs to the Trip's
  Country, and a Region already referenced by a Trip can never be moved
  to another Country. Existing Trip rows keep both `NULL`.

Country seed — the standard ISO 3166-1 set (249 officially assigned
alpha-2 codes; the code set is the ISO 3166-1 list as published in the
Debian iso-codes `iso3166-1` database), 1:1 ISO alpha-2 -> Russian
display name from Unicode CLDR 48.2.0, locale `ru`
(`cldr-json/cldr-localenames-full/main/ru/territories.json` at tag
`48.2.0` of https://github.com/unicode-org/cldr-json, sha256
305ba1f9eecb1b0151ab081a111aaa8a5898d38c9d57bfbfa125734e508a2389; the
default — non `-alt` — territory name). Each seeded row records that
origin as its provenance (`_SEED_SOURCE_TYPE`/`_SEED_SOURCE_REFERENCE`).
The rows are literal here, so a later CLDR release never changes existing
Country records automatically.

Region seed — exactly the 89 subjects of the Russian Federation listed in
the Constitution of the Russian Federation, Article 65, Part 1
(https://www.minjust.gov.ru/ru/documents/8011/), as the canonical
TourCRM RU administrative-subject seed fixed by the PO on 2026-10-05
(that host is not reachable from the build environment, so no remote
document was downloaded or hashed). `_RU_SUBJECTS` below is the pinned
repository snapshot of that list: every row has Country `RU`,
`semantic_type = administrative_subject`, `active = true` and the
provenance `_RU_SOURCE_TYPE`/`_RU_SOURCE_REFERENCE`. SHA-256 of the
snapshot (the 89 names in order, UTF-8, one per line, each followed by
`\n`) is `_RU_SNAPSHOT_SHA256` — the hash of this TourCRM seed snapshot,
not of a downloaded MinJust document. No Baikonur, no tourist/FSTR
regions, no other country.

Region `code` of the seed is an INTERNAL TOURCRM CODE: a stable ASCII
slug of the canonical name, unique within RU. Article 65 defines no
machine codes, and these codes carry no OKATO, OKTMO, ISO 3166-2 or any
other governmental-code meaning.

No permission is seeded: Geography reuses `trip.read`/`trip.manage`.

Downgrade drops the Trip columns and both catalogs (meant for an
installation that has not yet relied on Geography).
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c28e7b34462'
down_revision: Union[str, None] = '2a3f572d4f32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_SOURCE_TYPE = "ISO_3166_1"
_SEED_SOURCE_REFERENCE = "ISO 3166-1 alpha-2; Russian names: Unicode CLDR 48.2.0, locale ru"

_RU_SOURCE_TYPE = "CONSTITUTION_RF_ARTICLE_65"
_RU_SOURCE_REFERENCE = (
    "Constitution of the Russian Federation, Article 65, Part 1; canonical TourCRM RU "
    "administrative-subject seed fixed by PO on 2026-10-05"
)
_RU_SNAPSHOT_SHA256 = "07be4af5094d98aed298e08cf769cb5144bf33fe4bf3b1d6b723658228240e03"

# Constitution of the Russian Federation, Article 65, Part 1, in its order:
# (kind of subject, internal TourCRM code, canonical name). The kind is
# only documentation of the list's structure; it is not stored.
_RU_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    ('republic', 'ADYGEA', 'Республика Адыгея (Адыгея)'),
    ('republic', 'ALTAY_REPUBLIC', 'Республика Алтай'),
    ('republic', 'BASHKORTOSTAN', 'Республика Башкортостан'),
    ('republic', 'BURYATIA', 'Республика Бурятия'),
    ('republic', 'DAGESTAN', 'Республика Дагестан'),
    ('republic', 'DONETSK', 'Донецкая Народная Республика'),
    ('republic', 'INGUSHETIA', 'Республика Ингушетия'),
    ('republic', 'KABARDINO_BALKARIA', 'Кабардино-Балкарская Республика'),
    ('republic', 'KALMYKIA', 'Республика Калмыкия'),
    ('republic', 'KARACHAY_CHERKESSIA', 'Карачаево-Черкесская Республика'),
    ('republic', 'KARELIA', 'Республика Карелия'),
    ('republic', 'KOMI', 'Республика Коми'),
    ('republic', 'CRIMEA', 'Республика Крым'),
    ('republic', 'LUGANSK', 'Луганская Народная Республика'),
    ('republic', 'MARI_EL', 'Республика Марий Эл'),
    ('republic', 'MORDOVIA', 'Республика Мордовия'),
    ('republic', 'SAKHA', 'Республика Саха (Якутия)'),
    ('republic', 'NORTH_OSSETIA', 'Республика Северная Осетия — Алания'),
    ('republic', 'TATARSTAN', 'Республика Татарстан'),
    ('republic', 'TYVA', 'Республика Тыва'),
    ('republic', 'UDMURTIA', 'Удмуртская Республика'),
    ('republic', 'KHAKASSIA', 'Республика Хакасия'),
    ('republic', 'CHECHNYA', 'Чеченская Республика'),
    ('republic', 'CHUVASHIA', 'Чувашская Республика — Чувашия'),
    ('krai', 'ALTAY_KRAI', 'Алтайский край'),
    ('krai', 'ZABAYKALSKY_KRAI', 'Забайкальский край'),
    ('krai', 'KAMCHATKA_KRAI', 'Камчатский край'),
    ('krai', 'KRASNODAR_KRAI', 'Краснодарский край'),
    ('krai', 'KRASNOYARSK_KRAI', 'Красноярский край'),
    ('krai', 'PERM_KRAI', 'Пермский край'),
    ('krai', 'PRIMORSKY_KRAI', 'Приморский край'),
    ('krai', 'STAVROPOL_KRAI', 'Ставропольский край'),
    ('krai', 'KHABAROVSK_KRAI', 'Хабаровский край'),
    ('oblast', 'AMUR_OBLAST', 'Амурская область'),
    ('oblast', 'ARKHANGELSK_OBLAST', 'Архангельская область'),
    ('oblast', 'ASTRAKHAN_OBLAST', 'Астраханская область'),
    ('oblast', 'BELGOROD_OBLAST', 'Белгородская область'),
    ('oblast', 'BRYANSK_OBLAST', 'Брянская область'),
    ('oblast', 'VLADIMIR_OBLAST', 'Владимирская область'),
    ('oblast', 'VOLGOGRAD_OBLAST', 'Волгоградская область'),
    ('oblast', 'VOLOGDA_OBLAST', 'Вологодская область'),
    ('oblast', 'VORONEZH_OBLAST', 'Воронежская область'),
    ('oblast', 'ZAPOROZHYE_OBLAST', 'Запорожская область'),
    ('oblast', 'IVANOVO_OBLAST', 'Ивановская область'),
    ('oblast', 'IRKUTSK_OBLAST', 'Иркутская область'),
    ('oblast', 'KALININGRAD_OBLAST', 'Калининградская область'),
    ('oblast', 'KALUGA_OBLAST', 'Калужская область'),
    ('oblast', 'KEMEROVO_OBLAST', 'Кемеровская область — Кузбасс'),
    ('oblast', 'KIROV_OBLAST', 'Кировская область'),
    ('oblast', 'KOSTROMA_OBLAST', 'Костромская область'),
    ('oblast', 'KURGAN_OBLAST', 'Курганская область'),
    ('oblast', 'KURSK_OBLAST', 'Курская область'),
    ('oblast', 'LENINGRAD_OBLAST', 'Ленинградская область'),
    ('oblast', 'LIPETSK_OBLAST', 'Липецкая область'),
    ('oblast', 'MAGADAN_OBLAST', 'Магаданская область'),
    ('oblast', 'MOSCOW_OBLAST', 'Московская область'),
    ('oblast', 'MURMANSK_OBLAST', 'Мурманская область'),
    ('oblast', 'NIZHNY_NOVGOROD_OBLAST', 'Нижегородская область'),
    ('oblast', 'NOVGOROD_OBLAST', 'Новгородская область'),
    ('oblast', 'NOVOSIBIRSK_OBLAST', 'Новосибирская область'),
    ('oblast', 'OMSK_OBLAST', 'Омская область'),
    ('oblast', 'ORENBURG_OBLAST', 'Оренбургская область'),
    ('oblast', 'OREL_OBLAST', 'Орловская область'),
    ('oblast', 'PENZA_OBLAST', 'Пензенская область'),
    ('oblast', 'PSKOV_OBLAST', 'Псковская область'),
    ('oblast', 'ROSTOV_OBLAST', 'Ростовская область'),
    ('oblast', 'RYAZAN_OBLAST', 'Рязанская область'),
    ('oblast', 'SAMARA_OBLAST', 'Самарская область'),
    ('oblast', 'SARATOV_OBLAST', 'Саратовская область'),
    ('oblast', 'SAKHALIN_OBLAST', 'Сахалинская область'),
    ('oblast', 'SVERDLOVSK_OBLAST', 'Свердловская область'),
    ('oblast', 'SMOLENSK_OBLAST', 'Смоленская область'),
    ('oblast', 'TAMBOV_OBLAST', 'Тамбовская область'),
    ('oblast', 'TVER_OBLAST', 'Тверская область'),
    ('oblast', 'TOMSK_OBLAST', 'Томская область'),
    ('oblast', 'TULA_OBLAST', 'Тульская область'),
    ('oblast', 'TYUMEN_OBLAST', 'Тюменская область'),
    ('oblast', 'ULYANOVSK_OBLAST', 'Ульяновская область'),
    ('oblast', 'CHELYABINSK_OBLAST', 'Челябинская область'),
    ('oblast', 'YAROSLAVL_OBLAST', 'Ярославская область'),
    ('oblast', 'KHERSON_OBLAST', 'Херсонская область'),
    ('federal_city', 'MOSCOW', 'город федерального значения Москва'),
    ('federal_city', 'ST_PETERSBURG', 'город федерального значения Санкт-Петербург'),
    ('federal_city', 'SEVASTOPOL', 'город федерального значения Севастополь'),
    ('autonomous_oblast', 'JEWISH_AO', 'Еврейская автономная область'),
    ('autonomous_okrug', 'NENETS_AO', 'Ненецкий автономный округ'),
    ('autonomous_okrug', 'KHANTY_MANSI_AO', 'Ханты-Мансийский автономный округ — Югра'),
    ('autonomous_okrug', 'CHUKOTKA_AO', 'Чукотский автономный округ'),
    ('autonomous_okrug', 'YAMALO_NENETS_AO', 'Ямало-Ненецкий автономный округ'),
)

# ISO 3166-1 alpha-2 code -> canonical Russian name (see module docstring).
_COUNTRIES: tuple[tuple[str, str], ...] = (
    ('AD', 'Андорра'),
    ('AE', 'ОАЭ'),
    ('AF', 'Афганистан'),
    ('AG', 'Антигуа и Барбуда'),
    ('AI', 'Ангилья'),
    ('AL', 'Албания'),
    ('AM', 'Армения'),
    ('AO', 'Ангола'),
    ('AQ', 'Антарктида'),
    ('AR', 'Аргентина'),
    ('AS', 'Американское Самоа'),
    ('AT', 'Австрия'),
    ('AU', 'Австралия'),
    ('AW', 'Аруба'),
    ('AX', 'Аландские о-ва'),
    ('AZ', 'Азербайджан'),
    ('BA', 'Босния и Герцеговина'),
    ('BB', 'Барбадос'),
    ('BD', 'Бангладеш'),
    ('BE', 'Бельгия'),
    ('BF', 'Буркина-Фасо'),
    ('BG', 'Болгария'),
    ('BH', 'Бахрейн'),
    ('BI', 'Бурунди'),
    ('BJ', 'Бенин'),
    ('BL', 'Сен-Бартелеми'),
    ('BM', 'Бермудские о-ва'),
    ('BN', 'Бруней'),
    ('BO', 'Боливия'),
    ('BQ', 'Бонэйр, Синт-Эстатиус и Саба'),
    ('BR', 'Бразилия'),
    ('BS', 'Багамы'),
    ('BT', 'Бутан'),
    ('BV', 'о-в Буве'),
    ('BW', 'Ботсвана'),
    ('BY', 'Беларусь'),
    ('BZ', 'Белиз'),
    ('CA', 'Канада'),
    ('CC', 'Кокосовые о-ва'),
    ('CD', 'Конго - Киншаса'),
    ('CF', 'Центрально-Африканская Республика'),
    ('CG', 'Конго - Браззавиль'),
    ('CH', 'Швейцария'),
    ('CI', 'Кот-д’Ивуар'),
    ('CK', 'о-ва Кука'),
    ('CL', 'Чили'),
    ('CM', 'Камерун'),
    ('CN', 'Китай'),
    ('CO', 'Колумбия'),
    ('CR', 'Коста-Рика'),
    ('CU', 'Куба'),
    ('CV', 'Кабо-Верде'),
    ('CW', 'Кюрасао'),
    ('CX', 'о-в Рождества'),
    ('CY', 'Кипр'),
    ('CZ', 'Чехия'),
    ('DE', 'Германия'),
    ('DJ', 'Джибути'),
    ('DK', 'Дания'),
    ('DM', 'Доминика'),
    ('DO', 'Доминиканская Республика'),
    ('DZ', 'Алжир'),
    ('EC', 'Эквадор'),
    ('EE', 'Эстония'),
    ('EG', 'Египет'),
    ('EH', 'Западная Сахара'),
    ('ER', 'Эритрея'),
    ('ES', 'Испания'),
    ('ET', 'Эфиопия'),
    ('FI', 'Финляндия'),
    ('FJ', 'Фиджи'),
    ('FK', 'Фолклендские о-ва'),
    ('FM', 'Федеративные Штаты Микронезии'),
    ('FO', 'Фарерские о-ва'),
    ('FR', 'Франция'),
    ('GA', 'Габон'),
    ('GB', 'Великобритания'),
    ('GD', 'Гренада'),
    ('GE', 'Грузия'),
    ('GF', 'Французская Гвиана'),
    ('GG', 'Гернси'),
    ('GH', 'Гана'),
    ('GI', 'Гибралтар'),
    ('GL', 'Гренландия'),
    ('GM', 'Гамбия'),
    ('GN', 'Гвинея'),
    ('GP', 'Гваделупа'),
    ('GQ', 'Экваториальная Гвинея'),
    ('GR', 'Греция'),
    ('GS', 'Южная Георгия и Южные Сандвичевы о-ва'),
    ('GT', 'Гватемала'),
    ('GU', 'Гуам'),
    ('GW', 'Гвинея-Бисау'),
    ('GY', 'Гайана'),
    ('HK', 'Гонконг (САР)'),
    ('HM', 'о-ва Херд и Макдональд'),
    ('HN', 'Гондурас'),
    ('HR', 'Хорватия'),
    ('HT', 'Гаити'),
    ('HU', 'Венгрия'),
    ('ID', 'Индонезия'),
    ('IE', 'Ирландия'),
    ('IL', 'Израиль'),
    ('IM', 'о-в Мэн'),
    ('IN', 'Индия'),
    ('IO', 'Британская территория в Индийском океане'),
    ('IQ', 'Ирак'),
    ('IR', 'Иран'),
    ('IS', 'Исландия'),
    ('IT', 'Италия'),
    ('JE', 'Джерси'),
    ('JM', 'Ямайка'),
    ('JO', 'Иордания'),
    ('JP', 'Япония'),
    ('KE', 'Кения'),
    ('KG', 'Киргизия'),
    ('KH', 'Камбоджа'),
    ('KI', 'Кирибати'),
    ('KM', 'Коморы'),
    ('KN', 'Сент-Китс и Невис'),
    ('KP', 'КНДР'),
    ('KR', 'Республика Корея'),
    ('KW', 'Кувейт'),
    ('KY', 'о-ва Кайман'),
    ('KZ', 'Казахстан'),
    ('LA', 'Лаос'),
    ('LB', 'Ливан'),
    ('LC', 'Сент-Люсия'),
    ('LI', 'Лихтенштейн'),
    ('LK', 'Шри-Ланка'),
    ('LR', 'Либерия'),
    ('LS', 'Лесото'),
    ('LT', 'Литва'),
    ('LU', 'Люксембург'),
    ('LV', 'Латвия'),
    ('LY', 'Ливия'),
    ('MA', 'Марокко'),
    ('MC', 'Монако'),
    ('MD', 'Молдова'),
    ('ME', 'Черногория'),
    ('MF', 'Сен-Мартен'),
    ('MG', 'Мадагаскар'),
    ('MH', 'Маршалловы о-ва'),
    ('MK', 'Северная Македония'),
    ('ML', 'Мали'),
    ('MM', 'Мьянма (Бирма)'),
    ('MN', 'Монголия'),
    ('MO', 'Макао (САР)'),
    ('MP', 'Северные Марианские о-ва'),
    ('MQ', 'Мартиника'),
    ('MR', 'Мавритания'),
    ('MS', 'Монтсеррат'),
    ('MT', 'Мальта'),
    ('MU', 'Маврикий'),
    ('MV', 'Мальдивы'),
    ('MW', 'Малави'),
    ('MX', 'Мексика'),
    ('MY', 'Малайзия'),
    ('MZ', 'Мозамбик'),
    ('NA', 'Намибия'),
    ('NC', 'Новая Каледония'),
    ('NE', 'Нигер'),
    ('NF', 'о-в Норфолк'),
    ('NG', 'Нигерия'),
    ('NI', 'Никарагуа'),
    ('NL', 'Нидерланды'),
    ('NO', 'Норвегия'),
    ('NP', 'Непал'),
    ('NR', 'Науру'),
    ('NU', 'Ниуэ'),
    ('NZ', 'Новая Зеландия'),
    ('OM', 'Оман'),
    ('PA', 'Панама'),
    ('PE', 'Перу'),
    ('PF', 'Французская Полинезия'),
    ('PG', 'Папуа — Новая Гвинея'),
    ('PH', 'Филиппины'),
    ('PK', 'Пакистан'),
    ('PL', 'Польша'),
    ('PM', 'Сен-Пьер и Микелон'),
    ('PN', 'о-ва Питкэрн'),
    ('PR', 'Пуэрто-Рико'),
    ('PS', 'Палестинские территории'),
    ('PT', 'Португалия'),
    ('PW', 'Палау'),
    ('PY', 'Парагвай'),
    ('QA', 'Катар'),
    ('RE', 'Реюньон'),
    ('RO', 'Румыния'),
    ('RS', 'Сербия'),
    ('RU', 'Россия'),
    ('RW', 'Руанда'),
    ('SA', 'Саудовская Аравия'),
    ('SB', 'Соломоновы о-ва'),
    ('SC', 'Сейшельские о-ва'),
    ('SD', 'Судан'),
    ('SE', 'Швеция'),
    ('SG', 'Сингапур'),
    ('SH', 'о-в Св. Елены'),
    ('SI', 'Словения'),
    ('SJ', 'Шпицберген и Ян-Майен'),
    ('SK', 'Словакия'),
    ('SL', 'Сьерра-Леоне'),
    ('SM', 'Сан-Марино'),
    ('SN', 'Сенегал'),
    ('SO', 'Сомали'),
    ('SR', 'Суринам'),
    ('SS', 'Южный Судан'),
    ('ST', 'Сан-Томе и Принсипи'),
    ('SV', 'Сальвадор'),
    ('SX', 'Синт-Мартен'),
    ('SY', 'Сирия'),
    ('SZ', 'Эсватини'),
    ('TC', 'Тёркс и Кайкос'),
    ('TD', 'Чад'),
    ('TF', 'Французские Южные территории'),
    ('TG', 'Того'),
    ('TH', 'Таиланд'),
    ('TJ', 'Таджикистан'),
    ('TK', 'Токелау'),
    ('TL', 'Восточный Тимор'),
    ('TM', 'Туркменистан'),
    ('TN', 'Тунис'),
    ('TO', 'Тонга'),
    ('TR', 'Турция'),
    ('TT', 'Тринидад и Тобаго'),
    ('TV', 'Тувалу'),
    ('TW', 'Тайвань'),
    ('TZ', 'Танзания'),
    ('UA', 'Украина'),
    ('UG', 'Уганда'),
    ('UM', 'Внешние малые о-ва (США)'),
    ('US', 'Соединенные Штаты'),
    ('UY', 'Уругвай'),
    ('UZ', 'Узбекистан'),
    ('VA', 'Ватикан'),
    ('VC', 'Сент-Винсент и Гренадины'),
    ('VE', 'Венесуэла'),
    ('VG', 'Виргинские о-ва (Великобритания)'),
    ('VI', 'Виргинские о-ва (США)'),
    ('VN', 'Вьетнам'),
    ('VU', 'Вануату'),
    ('WF', 'Уоллис и Футуна'),
    ('WS', 'Самоа'),
    ('YE', 'Йемен'),
    ('YT', 'Майотта'),
    ('ZA', 'Южно-Африканская Республика'),
    ('ZM', 'Замбия'),
    ('ZW', 'Зимбабве'),
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('countries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=2), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('source_type', sa.String(length=64), nullable=True),
    sa.Column('source_reference', sa.String(length=500), nullable=True),
    sa.Column('first_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name='ck_countries_code_iso_alpha2'),
    sa.CheckConstraint('length(btrim(name)) > 0', name='ck_countries_name_not_blank'),
    sa.CheckConstraint('source_reference IS NULL OR length(btrim(source_reference)) > 0', name='ck_countries_source_reference_not_blank'),
    sa.CheckConstraint('source_type IS NULL OR length(btrim(source_type)) > 0', name='ck_countries_source_type_not_blank'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code', name='uq_countries_code')
    )
    op.create_table('regions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('country_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('semantic_type', sa.String(length=64), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('source_type', sa.String(length=64), nullable=True),
    sa.Column('source_reference', sa.String(length=500), nullable=True),
    sa.Column('first_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(code)) > 0', name='ck_regions_code_not_blank'),
    sa.CheckConstraint("semantic_type IN ('administrative_subject')", name='ck_regions_semantic_type'),
    sa.CheckConstraint('length(btrim(name)) > 0', name='ck_regions_name_not_blank'),
    sa.CheckConstraint('source_reference IS NULL OR length(btrim(source_reference)) > 0', name='ck_regions_source_reference_not_blank'),
    sa.CheckConstraint('source_type IS NULL OR length(btrim(source_type)) > 0', name='ck_regions_source_type_not_blank'),
    sa.ForeignKeyConstraint(['country_id'], ['countries.id'], name='fk_regions_country_id', onupdate='RESTRICT', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('country_id', 'code', name='uq_regions_country_id_code'),
    sa.UniqueConstraint('id', 'country_id', name='uq_regions_id_country_id')
    )
    op.add_column('trips', sa.Column('country_id', sa.UUID(), nullable=True))
    op.add_column('trips', sa.Column('region_id', sa.UUID(), nullable=True))
    op.create_index('ix_trips_country_id', 'trips', ['country_id'], unique=False)
    op.create_index('ix_trips_region_id', 'trips', ['region_id'], unique=False)
    op.create_foreign_key('fk_trips_region_id_country_id', 'trips', 'regions', ['region_id', 'country_id'], ['id', 'country_id'], onupdate='RESTRICT', ondelete='RESTRICT')
    op.create_foreign_key('fk_trips_country_id', 'trips', 'countries', ['country_id'], ['id'], onupdate='RESTRICT', ondelete='RESTRICT')
    op.create_check_constraint(
        'ck_trips_region_requires_country', 'trips', 'region_id IS NULL OR country_id IS NOT NULL'
    )
    # ### end Alembic commands ###

    countries = sa.table(
        'countries',
        sa.column('id', sa.UUID()),
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('active', sa.Boolean()),
        sa.column('source_type', sa.String()),
        sa.column('source_reference', sa.String()),
    )
    country_ids = {code: uuid.uuid4() for code, _name in _COUNTRIES}
    op.bulk_insert(
        countries,
        [
            {
                'id': country_ids[code],
                'code': code,
                'name': name,
                'active': True,
                'source_type': _SEED_SOURCE_TYPE,
                'source_reference': _SEED_SOURCE_REFERENCE,
            }
            for code, name in _COUNTRIES
        ],
    )

    regions = sa.table(
        'regions',
        sa.column('id', sa.UUID()),
        sa.column('country_id', sa.UUID()),
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('semantic_type', sa.String()),
        sa.column('active', sa.Boolean()),
        sa.column('source_type', sa.String()),
        sa.column('source_reference', sa.String()),
    )
    op.bulk_insert(
        regions,
        [
            {
                'id': uuid.uuid4(),
                'country_id': country_ids['RU'],
                'code': code,
                'name': name,
                'semantic_type': 'administrative_subject',
                'active': True,
                'source_type': _RU_SOURCE_TYPE,
                'source_reference': _RU_SOURCE_REFERENCE,
            }
            for _kind, code, name in _RU_SUBJECTS
        ],
    )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('ck_trips_region_requires_country', 'trips', type_='check')
    op.drop_constraint('fk_trips_country_id', 'trips', type_='foreignkey')
    op.drop_constraint('fk_trips_region_id_country_id', 'trips', type_='foreignkey')
    op.drop_index('ix_trips_region_id', table_name='trips')
    op.drop_index('ix_trips_country_id', table_name='trips')
    op.drop_column('trips', 'region_id')
    op.drop_column('trips', 'country_id')
    op.drop_table('regions')
    op.drop_table('countries')
    # ### end Alembic commands ###
