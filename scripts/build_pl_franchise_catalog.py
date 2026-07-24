#!/usr/bin/env python3
"""Build an auditable Polish franchise-directory snapshot from saved HTML.

The output is a lead catalogue, not a claim that every listed offer is active.
It intentionally does not copy financial figures or publishers' descriptions.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlsplit


PRIMARY_URL = "https://franchising.pl/katalog/wszystkie/"
SECONDARY_URL = "https://franczyzawpolsce.pl/baza-sieci/"

CATEGORY_NAMES = {
    "automotive": "Motoryzacja",
    "beauty": "Uroda i kosmetyki",
    "business-services": "Usługi dla biznesu",
    "education": "Edukacja",
    "fashion": "Odzież i obuwie",
    "finance": "Finanse i ubezpieczenia",
    "food-retail": "Artykuły spożywcze",
    "gastronomy": "Gastronomia",
    "health-fitness": "Sport i zdrowie",
    "home-garden": "Dom i ogród",
    "property": "Nieruchomości",
    "retail": "Handel detaliczny",
    "services": "Usługi dla konsumentów",
    "other": "Pozostałe",
}

SECONDARY_CATEGORY_MAP = {
    "akcesoria-i-dodatki": "retail",
    "artykuly-spozywcze": "food-retail",
    "edukacja": "education",
    "finanse-i-ubezpieczenia": "finance",
    "gastronomia": "gastronomy",
    "motoryzacja": "automotive",
    "nieruchomosci": "property",
    "odziez-i-obuwie": "fashion",
    "sport-i-zdrowie": "health-fitness",
    "uroda-i-kosmetyki": "beauty",
    "uslugi-dla-biznesu": "business-services",
    "uslugi-dla-klientow-indywidualnych": "services",
    "wszystko-dla-domu-i-ogrodu": "home-garden",
}

KEYWORDS = (
    ("gastronomy", ("restaur", "pizza", "kebab", "burger", "kawiarn", "lod", "gastronom", "food", "bar ", "sushi")),
    ("food-retail", ("spożyw", "supermarket", "sklep convenience", "piekar", "alkohol", "żywno")),
    ("education", ("eduk", "kurs", "szkoł", "przedszkol", "nauka", "korepety")),
    ("beauty", ("urod", "kosmet", "fryz", "salon pięk", "depil")),
    ("health-fitness", ("fitness", "sport", "trening", "medycz", "zdrow", "rehabilit")),
    ("finance", ("bank", "ubezpiec", "finans", "pożycz", "kredyt")),
    ("property", ("nieruchomo", "mieszk", "pośrednictwo")),
    ("automotive", ("samoch", "motoryz", "opon", "auto ", "myjni")),
    ("fashion", ("odzież", "obuw", "moda", "ubran")),
    ("home-garden", ("dom i ogród", "mebl", "remont", "wyposażenie wnętrz")),
    ("business-services", ("marketing", "księgow", "biznes", "biuro", "rekrut")),
    ("services", ("usług", "przesył", "sprząt", "opieka", "serwis")),
    ("retail", ("sklep", "stoiska", "sprzedaż", "handel")),
)


def plain(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    folded = folded.casefold().replace("&", " and ").replace("+", " plus ")
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return slug[:190] or "franczyza"


def identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slugify(value).replace("plus", ""))


def infer_category(description: str) -> str:
    lowered = description.casefold()
    for category, words in KEYWORDS:
        if any(word in lowered for word in words):
            return category
    return "other"


def source(url: str, observed_at: str, *, publisher: str, supports: list[str]) -> dict:
    return {
        "url": url,
        "publisher": publisher,
        "source_type": "directory_listing",
        "observed_at": observed_at,
        "supports": supports,
    }


def parse_primary(path: Path, observed_at: str) -> list[dict]:
    body = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r'<a\s+href="(?P<href>/franczyza/[^"]+)"[^>]*>\s*'
        r'<div\s+class="franchise-item">(?P<body>.*?)</div>\s*</div>\s*</a>',
        re.I | re.S,
    )
    records = []
    for match in pattern.finditer(body):
        fragment = match.group("body")
        name_match = re.search(r"<h3>(.*?)</h3>", fragment, re.I | re.S)
        description_match = re.search(r"<p>(.*?)</p>", fragment, re.I | re.S)
        if not name_match:
            continue
        name = plain(name_match.group(1))
        description = plain(description_match.group(1)) if description_match else ""
        url = urljoin(PRIMARY_URL, match.group("href"))
        records.append(
            {
                "name": name,
                "aliases": [],
                "category_key": infer_category(description),
                "listing_note": description[:240],
                "market_status": "listed",
                "recruitment_status": "listed_offer",
                "is_active": True,
                "website_url": "",
                "website_url_status": "missing",
                "sources": [source(url, observed_at, publisher="Franchising.pl", supports=["directory_presence", "recruitment_listing"])],
            }
        )
    if len(records) < 150:
        raise ValueError(f"Primary catalogue yielded only {len(records)} records; markup likely changed.")
    return records


def parse_secondary(paths: list[Path], observed_at: str) -> list[dict]:
    pattern = re.compile(r"<a\s+class=['\"]BS-title['\"]\s+href=['\"]([^'\"]+)['\"]>(.*?)</a>", re.I | re.S)
    records = []
    for path in paths:
        body = path.read_text(encoding="utf-8", errors="replace")
        for url, raw_name in pattern.findall(body):
            name = plain(raw_name)
            parts = [part for part in urlsplit(url).path.split("/") if part]
            category_key = SECONDARY_CATEGORY_MAP.get(parts[1] if len(parts) > 2 else "", "other")
            records.append(
                {
                    "name": name,
                    "aliases": [],
                    "category_key": category_key,
                    "listing_note": "",
                    "market_status": "listed",
                    "recruitment_status": "listed_offer",
                    "is_active": True,
                    "website_url": "",
                    "website_url_status": "missing",
                    "sources": [source(url, observed_at, publisher="Franczyza w Polsce", supports=["directory_presence", "recruitment_listing"])],
                }
            )
    if len(records) < 20:
        raise ValueError(f"Secondary catalogue yielded only {len(records)} records; markup likely changed.")
    return records


def merge_records(primary: list[dict], secondary: list[dict], observed_at: str) -> list[dict]:
    merged: dict[str, dict] = {}
    for record in [*primary, *secondary]:
        key = identity(record["name"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
            continue
        if record["name"] != existing["name"] and record["name"] not in existing["aliases"]:
            existing["aliases"].append(record["name"])
        existing["sources"].extend(
            item for item in record["sources"] if item["url"] not in {entry["url"] for entry in existing["sources"]}
        )
        if existing["category_key"] == "other" and record["category_key"] != "other":
            existing["category_key"] = record["category_key"]

    # Explicitly documented inactive exception. Directory absence alone never
    # changes a brand to inactive.
    north_fish = merged.get(identity("North Fish"))
    if north_fish is None:
        north_fish = {
            "name": "North Fish",
            "aliases": [],
            "category_key": "gastronomy",
            "listing_note": "",
            "website_url": "",
            "website_url_status": "missing",
            "sources": [],
        }
        merged[identity("North Fish")] = north_fish
    north_fish.update(market_status="inactive", recruitment_status="not_recruiting", is_active=False)
    north_fish["sources"].extend(
            [
                {
                    "url": "https://www.rp.pl/ekonomia/art44470521-upadek-sieci-north-fish-pracownicy-czekaja-na-wynagrodzenie",
                    "publisher": "Rzeczpospolita",
                    "source_type": "independent_media",
                    "observed_at": observed_at,
                    "supports": ["inactive_market_status"],
                },
                {
                    "url": "https://www.trojmiasto.pl/biznes/North-Fish-znika-z-Trojmiasta-Siec-zamyka-lokale-w-Gdansku-i-Gdyni-n216796.html",
                    "publisher": "Trojmiasto.pl",
                    "source_type": "independent_media",
                    "observed_at": observed_at,
                    "supports": ["inactive_market_status"],
                },
            ]
        )

    # Current market presence can outlive a public individual-franchise offer.
    # Keep these real networks in the portal while leaving recruitment unknown.
    current_networks = (
        (
            "KFC", "https://kfc.pl/", "https://kfc.pl/o-nas",
            "Official Polish consumer site confirms current market presence.",
        ),
        (
            "Subway", "https://mysubway.pl/", "https://amicenergy.pl/pl/produkty-i-uslugi/subway",
            "Current operator page confirms Subway locations in Poland.",
        ),
        (
            "Zahir Kebab", "https://zahirkebab.pl/", "https://zahirkebab.pl/o-nas/",
            "Official brand page confirms the operating Polish network.",
        ),
    )
    for name, website, evidence_url, note in current_networks:
        key = identity(name)
        record = merged.get(key)
        if record is None:
            record = {
                "name": name,
                "aliases": [],
                "category_key": "gastronomy",
                "listing_note": note,
                "sources": [],
            }
            merged[key] = record
        record.update(
            market_status="active",
            recruitment_status="unknown",
            is_active=True,
            website_url=website,
            website_url_status="unverified_seed",
        )
        if evidence_url not in {item["url"] for item in record["sources"]}:
            record["sources"].append(
                {
                    "url": evidence_url,
                    "publisher": name,
                    "source_type": "official_brand",
                    "observed_at": observed_at,
                    "supports": ["active_market_status"],
                }
            )

    # Supplemental real-market leads already known to the portal. Some are not
    # advertising an individual franchise offer in either current directory.
    # Their official-looking URL is therefore stored only as an unverified seed
    # and their status stays uncertain until the L1 pipeline validates it.
    supplemental = (
        ("abc", "abc", "https://www.sklepyabc.pl", "food-retail", []),
        ("5 a sec", "5asec", "https://www.5asec.pl", "services", []),
        ("Akademia Bystrzak", "akademia-bystrzak", "https://bystrzak.edu.pl", "education", []),
        ("Bricks 4 Kidz", "bricks-4-kidz", "https://www.bricks4kidz.com", "education", []),
        ("CityFit", "cityfit", "https://cityfit.pl", "health-fitness", []),
        ("CleanWhale", "cleanwhale", "https://cleanwhale.pl", "services", []),
        ("Code Kids", "code-kids", "https://codekids.pl", "education", []),
        ("Crazy Bubble", "crazy-bubble", "https://crazybubble.pl", "gastronomy", []),
        ("Da Vinci", "da-vinci", "https://davinci.edu.pl", "education", []),
        ("Groszek", "groszek", "https://www.groszek.com.pl", "food-retail", []),
        ("Helen Doron English", "helen-doron", "https://www.helendoron.pl", "education", ["Helen Doron English / Español"]),
        ("Kołacz na Okrągło", "kolacz-na-okraglo", "https://kolaczonakraglo.pl", "gastronomy", []),
        ("Lewiatan", "lewiatan", "https://www.lewiatan.pl", "food-retail", ["Polska Sieć Handlowa Lewiatan"]),
        ("Lodolandia", "lodolandia", "https://lodolandia.pl", "gastronomy", ["Lodolandia & Kołacz"]),
        ("Lody Bonano", "lody-bonano", "https://lodybonano.pl", "gastronomy", []),
        ("Makarun", "makarun", "https://makarun.pl", "gastronomy", []),
        ("MathRiders", "mathriders", "https://mathriders.pl", "education", ["MathRiders Polska"]),
        ("Nasz Sklep", "nasz-sklep", "https://www.naszsklep.pl", "food-retail", []),
        ("Piekarnia Grzybki", "piekarnia-grzybki", "https://www.grzybki.pl", "food-retail", []),
        ("Pizzeria Biesiadowo", "pizzeria-biesiadowo", "https://biesiadowo.pl", "gastronomy", []),
        ("RE/MAX", "remax", "https://www.remax-polska.pl", "property", ["Re/Max Polska"]),
        ("SPAR", "spar", "https://www.spar.pl", "food-retail", []),
        ("Sphinx", "sphinx", "https://sphinx.pl", "gastronomy", []),
        ("YASUMI", "yasumi", "https://yasumi.pl", "beauty", ["Yasumi Instytut Zdrowia i Urody"]),
        ("epaka.pl", "epaka", "https://www.epaka.pl", "services", []),
    )
    for name, preferred_slug, website, category_key, matching_names in supplemental:
        matching_keys = {identity(name), *(identity(item) for item in matching_names)}
        candidate_items = [(key, record) for key, record in merged.items() if key in matching_keys]
        candidates = [record for _, record in candidate_items]
        record = candidates[0] if candidates else None
        if record is None:
            record = {
                "name": name,
                "aliases": [],
                "category_key": category_key,
                "listing_note": "Existing Polish market lead; current status requires L1 validation.",
                "market_status": "uncertain",
                "recruitment_status": "unknown",
                "is_active": True,
                "sources": [],
            }
            merged[identity(name)] = record
        else:
            for duplicate_key, duplicate in candidate_items[1:]:
                record["sources"].extend(
                    item
                    for item in duplicate["sources"]
                    if item["url"] not in {source_item["url"] for source_item in record["sources"]}
                )
                if duplicate["name"] not in record["aliases"]:
                    record["aliases"].append(duplicate["name"])
                merged.pop(duplicate_key, None)
            previous_name = record["name"]
            if previous_name != name and previous_name not in record["aliases"]:
                record["aliases"].append(previous_name)
            record["name"] = name
        record["preferred_slug"] = preferred_slug
        record["website_url"] = website
        record["website_url_status"] = "unverified_seed"
        if website not in {item["url"] for item in record["sources"]}:
            record["sources"].append(
                {
                    "url": website,
                    "publisher": name,
                    "source_type": "unverified_official_candidate",
                    "observed_at": observed_at,
                    "supports": ["market_lead", "website_seed"],
                }
            )

    used_slugs: set[str] = set()
    result = []
    for record in sorted(merged.values(), key=lambda item: item["name"].casefold()):
        base = record.pop("preferred_slug", None) or slugify(record["name"])
        slug = base
        suffix = 2
        while slug in used_slugs:
            slug = f"{base[:185]}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        record["slug"] = slug
        record["category_name"] = CATEGORY_NAMES[record["category_key"]]
        record["short_description"] = f"Wpis katalogowy: {record['category_name'].lower()}. Status oferty wymaga walidacji w researchu."
        result.append(record)
    return result


def write_outputs(records: list[dict], observed_at: str, json_path: Path, csv_path: Path) -> None:
    payload = {
        "schema_version": "1.0.0",
        "country": "PL",
        "snapshot_date": observed_at,
        "scope": "Best-effort multi-source snapshot of public franchise offer directories in Poland; not an official registry.",
        "completeness_caveat": "Poland has no complete public official franchise registry. Listed means observed in a current directory, not independently verified active.",
        "sources": [PRIMARY_URL, SECONDARY_URL],
        "record_count": len(records),
        "records": records,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["name", "slug", "aliases", "category_key", "category_name", "market_status", "recruitment_status", "is_active", "website_url", "website_url_status", "source_urls"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                **{field: record.get(field, "") for field in fields},
                "aliases": " | ".join(record["aliases"]),
                "source_urls": " | ".join(item["url"] for item in record["sources"]),
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--secondary", type=Path, action="append", required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    records = merge_records(parse_primary(args.primary, args.snapshot_date), parse_secondary(args.secondary, args.snapshot_date), args.snapshot_date)
    write_outputs(records, args.snapshot_date, args.output_json, args.output_csv)
    print(json.dumps({"records": len(records), "json": str(args.output_json), "csv": str(args.output_csv)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
