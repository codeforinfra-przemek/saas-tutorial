from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from franchises.models import Franchise, FranchiseCategory


DEFAULT_CATALOG = (
    Path(settings.BASE_DIR).parents[1]
    / "data"
    / "franchise_catalog"
    / "pl_franchises_2026-07-22.json"
)

CATEGORY_SORT = {
    "gastronomy": 10,
    "food-retail": 20,
    "retail": 30,
    "services": 40,
    "education": 50,
    "health-fitness": 60,
    "beauty": 70,
    "fashion": 80,
    "finance": 90,
    "property": 100,
    "automotive": 110,
    "home-garden": 120,
    "business-services": 130,
    "other": 999,
}


def _has_relation(instance, related_name: str) -> bool:
    manager = getattr(instance, related_name, None)
    return bool(manager is not None and manager.exists())


def preservation_reasons(franchise: Franchise) -> list[str]:
    reasons = []
    if franchise.organization_id:
        reasons.append("organization")
    if franchise.data_status != Franchise.DATA_STATUS_DEMO:
        reasons.append(f"data_status:{franchise.data_status}")
    for relation in (
        "research_imports",
        "research_workspaces",
        "research_launches",
        "research_published_fields",
        "update_requests",
        "claim_requests",
        "leads",
        "subscriptions",
        "sales_accounts",
        "sales_opportunities",
    ):
        try:
            if _has_relation(franchise, relation):
                reasons.append(relation)
        except (AttributeError, TypeError):
            continue
    return reasons


def load_catalog(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandError(f"Nie można odczytać katalogu {path}: {exc}") from exc
    if payload.get("schema_version") != "1.0.0" or payload.get("country") != "PL":
        raise CommandError("Nieobsługiwany schemat lub kraj katalogu.")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise CommandError("Katalog nie zawiera rekordów.")
    required = {
        "name", "slug", "category_key", "category_name", "short_description",
        "market_status", "recruitment_status", "is_active", "website_url",
        "website_url_status", "sources",
    }
    seen = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or required - set(record):
            raise CommandError(f"Rekord {index} nie spełnia schematu.")
        if record["slug"] in seen:
            raise CommandError(f"Powtórzony slug: {record['slug']}")
        seen.add(record["slug"])
        if record["market_status"] not in dict(Franchise.MARKET_STATUS_CHOICES):
            raise CommandError(f"Nieznany market_status w rekordzie {index}.")
        if record["recruitment_status"] not in dict(Franchise.RECRUITMENT_STATUS_CHOICES):
            raise CommandError(f"Nieznany recruitment_status w rekordzie {index}.")
        if record["website_url_status"] not in dict(Franchise.WEBSITE_STATUS_CHOICES):
            raise CommandError(f"Nieznany website_url_status w rekordzie {index}.")
    if payload.get("record_count") != len(records):
        raise CommandError("record_count nie odpowiada liczbie rekordów.")
    return payload


class Command(BaseCommand):
    help = "Bezpiecznie synchronizuje audytowalny snapshot katalogu franczyz PL."

    def add_arguments(self, parser):
        parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
        parser.add_argument(
            "--backup-dir",
            type=Path,
            default=Path(settings.BASE_DIR).parents[1] / "data" / "catalog_backups",
        )
        parser.add_argument("--apply", action="store_true", help="Zapisz zmiany; domyślnie dry-run.")
        parser.add_argument(
            "--prune-unresearched",
            action="store_true",
            help="Usuń stare profile demo bez researchu i danych operacyjnych przed importem.",
        )
        parser.add_argument(
            "--confirm-prune-unresearched",
            action="store_true",
            help="Drugie, jawne potwierdzenie destrukcyjnego czyszczenia.",
        )
        parser.add_argument(
            "--prune-operational-placeholders",
            action="store_true",
            help=(
                "Usuń także nieobjęte snapshotem profile demo z powiązaniami "
                "operacyjnymi. Research nadal jest bezwzględnie chroniony."
            ),
        )

    def handle(self, *args, **options):
        catalog_path = options["catalog"].resolve()
        payload = load_catalog(catalog_path)
        records = payload["records"]
        apply_changes = options["apply"]
        prune = options["prune_unresearched"]
        if prune and apply_changes and not options["confirm_prune_unresearched"]:
            raise CommandError("Dodaj --confirm-prune-unresearched, aby potwierdzić usuwanie.")

        existing = list(Franchise.objects.all().order_by("id"))
        catalog_slugs = {record["slug"] for record in records}
        reason_map = {item.pk: preservation_reasons(item) for item in existing}
        research_relations = {
            "research_imports", "research_workspaces", "research_launches",
            "research_published_fields",
        }
        research_protected = {
            item.pk: [reason for reason in reason_map[item.pk] if reason in research_relations]
            for item in existing
            if any(reason in research_relations for reason in reason_map[item.pk])
        }
        operational_protected = {
            item.pk: reason_map[item.pk]
            for item in existing
            if reason_map[item.pk] and item.pk not in research_protected
        }
        force_operational = options["prune_operational_placeholders"]
        deletable = [
            item
            for item in existing
            if item.slug not in catalog_slugs
            and item.pk not in research_protected
            and (force_operational or item.pk not in operational_protected)
        ]
        protected = {
            **({} if force_operational else operational_protected),
            **research_protected,
        }
        summary = {
            "mode": "apply" if apply_changes else "dry_run",
            "snapshot_date": payload["snapshot_date"],
            "catalog_records": len(records),
            "existing_profiles": len(existing),
            "preserved_profiles": len(protected),
            "research_protected_profiles": len(research_protected),
            "operational_placeholders_detected": len(operational_protected),
            "operational_placeholders_protected": (
                0 if force_operational else len(operational_protected)
            ),
            "deletable_unresearched_profiles": len(deletable),
            "prune_requested": prune,
        }
        if not apply_changes:
            summary["would_delete"] = [item.slug for item in deletable] if prune else []
            summary["preserved"] = [
                {"slug": item.slug, "reasons": protected[item.pk]}
                for item in existing if item.pk in protected
            ]
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        snapshot_date = date.fromisoformat(payload["snapshot_date"])
        imported_at = timezone.now()
        created = updated = deleted = 0
        backup_path = None
        with transaction.atomic():
            if prune and deletable:
                backup_root = options["backup_dir"].resolve()
                backup_root.mkdir(parents=True, exist_ok=True)
                backup_path = backup_root / f"franchises-before-sync-{imported_at:%Y%m%dT%H%M%SZ}.json"
                backup = [
                    {
                        "id": item.pk,
                        "name": item.name,
                        "slug": item.slug,
                        "data_status": item.data_status,
                        "website_url": item.website_url,
                    }
                    for item in deletable
                ]
                backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                deleted = len(deletable)
                Franchise.objects.filter(pk__in=[item.pk for item in deletable]).delete()

            for record in records:
                category, _ = FranchiseCategory.objects.get_or_create(
                    slug=record["category_key"],
                    defaults={
                        "name": record["category_name"],
                        "sort_order": CATEGORY_SORT.get(record["category_key"], 999),
                    },
                )
                franchise = Franchise.objects.filter(slug=record["slug"]).first()
                defaults = {
                    "name": record["name"][:180],
                    "category": category,
                    "short_description": record["short_description"][:260],
                    "market_status": record["market_status"],
                    "recruitment_status": record["recruitment_status"],
                    "market_status_checked_at": snapshot_date,
                    "catalog_sources": record["sources"],
                    "catalog_imported_at": imported_at,
                    "is_active": bool(record["is_active"]),
                    "is_verified": False,
                }
                if franchise is None:
                    Franchise.objects.create(
                        slug=record["slug"],
                        website_url=record["website_url"],
                        website_url_status=record["website_url_status"],
                        data_status=Franchise.DATA_STATUS_DEMO,
                        **defaults,
                    )
                    created += 1
                    continue

                # Never replace researched/editor/vendor profile content with a
                # directory lead. Catalogue metadata is safe to refresh.
                franchise.market_status = defaults["market_status"]
                franchise.recruitment_status = defaults["recruitment_status"]
                franchise.market_status_checked_at = snapshot_date
                franchise.catalog_sources = record["sources"]
                franchise.catalog_imported_at = imported_at
                if not preservation_reasons(franchise):
                    for field in ("name", "category", "short_description", "is_active"):
                        setattr(franchise, field, defaults[field])
                if record["market_status"] == Franchise.MARKET_STATUS_INACTIVE:
                    franchise.is_active = False
                franchise.save(update_fields=[
                    "market_status", "recruitment_status", "market_status_checked_at",
                    "catalog_sources", "catalog_imported_at", "name", "category",
                    "short_description", "is_active", "updated_at",
                ])
                updated += 1

        summary.update(created=created, updated=updated, deleted=deleted)
        if backup_path:
            summary["backup_path"] = str(backup_path)
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
