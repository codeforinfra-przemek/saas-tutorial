from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Organization
from billing.models import OrganizationSubscription, Plan
from franchises.models import Franchise

from backoffice.models import RevenueEvent, SalesAccount, SalesActivity, SalesContact, SalesOpportunity


class Command(BaseCommand):
    help = "Create repeatable demo revenue and internal sales CRM data."

    def handle(self, *args, **options):
        now = timezone.now()
        plans = self._plans()
        franchises = list(Franchise.objects.filter(is_active=True).select_related("organization")[:12])
        subscription_specs = (
            ("demo-revenue-zabka", "Żabka Revenue Demo", "pro", "yearly", 11, 90),
            ("demo-revenue-mcd", "McDonald's Revenue Demo", "growth", "monthly", 4, 25),
            ("demo-revenue-coffee", "Coffee Network Revenue Demo", "basic", "monthly", 2, 12),
            ("demo-revenue-fitness", "Fitness Group Revenue Demo", "growth", "yearly", 8, 190),
            ("demo-revenue-education", "Education Hub Revenue Demo", "basic", "monthly", 1, 5),
            ("demo-revenue-services", "Business Services Revenue Demo", "pro", "yearly", 7, 300),
        )
        organizations = []
        for index, (slug, name, plan_slug, interval, months_ago, days_to_end) in enumerate(subscription_specs):
            organization, _ = Organization.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "status": Organization.STATUS_ACTIVE, "package_type": Organization.PACKAGE_PREMIUM},
            )
            organizations.append(organization)
            subscription, _ = OrganizationSubscription.objects.update_or_create(
                organization=organization,
                plan=plans[plan_slug],
                defaults={
                    "status": OrganizationSubscription.STATUS_ACTIVE,
                    "billing_interval": interval,
                    "starts_at": now - timedelta(days=months_ago * 30),
                    "ends_at": now + timedelta(days=days_to_end),
                    "manual_payment_status": OrganizationSubscription.PAYMENT_PAID,
                },
            )
            mrr = self._mrr(subscription)
            self._event(
                organization,
                subscription,
                plans[plan_slug],
                f"new-{slug}",
                RevenueEvent.EVENT_NEW_SUBSCRIPTION,
                now - timedelta(days=months_ago * 30),
                interval,
                mrr,
                "Początek demonstracyjnej subskrypcji.",
            )
            if index in (0, 3):
                self._event(
                    organization,
                    subscription,
                    plans[plan_slug],
                    f"renewal-{slug}",
                    RevenueEvent.EVENT_RENEWAL,
                    now - timedelta(days=20),
                    interval,
                    Decimal("0"),
                    "Odnowienie bez zmiany MRR.",
                )

        cancelled_org, _ = Organization.objects.update_or_create(
            slug="demo-revenue-churned",
            defaults={"name": "Former Partner Revenue Demo", "status": Organization.STATUS_INACTIVE, "package_type": Organization.PACKAGE_BASIC},
        )
        cancelled_subscription, _ = OrganizationSubscription.objects.update_or_create(
            organization=cancelled_org,
            plan=plans["growth"],
            defaults={
                "status": OrganizationSubscription.STATUS_CANCELLED,
                "billing_interval": "monthly",
                "starts_at": now - timedelta(days=180),
                "ends_at": now - timedelta(days=4),
                "manual_payment_status": OrganizationSubscription.PAYMENT_PAID,
            },
        )
        churn_mrr = plans["growth"].price_monthly or Decimal("0")
        self._event(cancelled_org, cancelled_subscription, plans["growth"], "new-churned", RevenueEvent.EVENT_NEW_SUBSCRIPTION, now - timedelta(days=180), "monthly", churn_mrr, "Dawna subskrypcja demonstracyjna.")
        self._event(cancelled_org, cancelled_subscription, plans["growth"], "churn-churned", RevenueEvent.EVENT_CHURN, now - timedelta(days=4), "monthly", -churn_mrr, "Klient zakończył współpracę po zmianie priorytetów marketingowych.")

        stages = (
            ("Żabka: rozbudowa profilu", SalesOpportunity.STAGE_NEGOTIATION, Decimal("899"), 85, -1, -2),
            ("McDonald's: pakiet Promocja", SalesOpportunity.STAGE_PROPOSAL, Decimal("499"), 65, 3, -8),
            ("Carrefour: profil lokalizacji", SalesOpportunity.STAGE_DISCOVERY, Decimal("199"), 40, -3, -18),
            ("Da Grasso: kampania premium", SalesOpportunity.STAGE_CONTACTED, Decimal("899"), 25, 7, -20),
            ("Subway: wznowienie widoczności", SalesOpportunity.STAGE_CHURN_RISK, Decimal("499"), 55, -2, -16),
        )
        for index, (title, stage, monthly_value, probability, follow_up_days, last_activity_days) in enumerate(stages):
            organization = organizations[index % len(organizations)]
            franchise = franchises[index % len(franchises)] if franchises else None
            account, _ = SalesAccount.objects.update_or_create(
                name=title.split(":")[0],
                defaults={"organization": organization, "franchise": franchise, "status": SalesAccount.STATUS_PROSPECT, "source": ("inbound" if index % 2 else "claim_profile"), "next_follow_up_at": now + timedelta(days=follow_up_days), "last_activity_at": now - timedelta(days=abs(last_activity_days)), "notes": "Konto demonstracyjne do pracy zespołu sprzedażowego."},
            )
            contact, _ = SalesContact.objects.update_or_create(
                account=account,
                email=f"contact{index + 1}@example.com",
                defaults={"name": f"Kontakt demonstracyjny {index + 1}", "role": "Marketing / rozwój sieci", "phone": f"+48 500 700 {100 + index}", "is_primary": True},
            )
            opportunity, _ = SalesOpportunity.objects.update_or_create(
                account=account,
                title=title,
                defaults={"organization": organization, "franchise": franchise, "stage": stage, "expected_monthly_value": monthly_value, "expected_annual_value": monthly_value * Decimal("12"), "probability": probability, "expected_close_date": (now + timedelta(days=30 + index * 12)).date(), "next_follow_up_at": now + timedelta(days=follow_up_days), "last_activity_at": now - timedelta(days=abs(last_activity_days)), "notes": "Demonstracyjna szansa sprzedażowa. Wymaga dalszego kontaktu i doprecyzowania zakresu pakietu."},
            )
            self._activity(account, opportunity, contact, f"note-{index}", SalesActivity.TYPE_NOTE, "Notatka po rozmowie", "Ustalono kolejne kroki i zakres danych potrzebnych do przedstawienia oferty.", now - timedelta(days=abs(last_activity_days)))
            self._activity(account, opportunity, contact, f"task-{index}", SalesActivity.TYPE_TASK, "Follow-up", "Skontaktuj się z klientem w sprawie decyzji o pakiecie.", now - timedelta(days=abs(last_activity_days) - 1), due_at=now + timedelta(days=follow_up_days))

        self.stdout.write(self.style.SUCCESS("Backoffice demo data is ready: revenue, subscriptions and sales pipeline."))

    def _plans(self):
        defaults = {
            "basic": ("Profil", Decimal("199"), Decimal("1990")),
            "growth": ("Promocja", Decimal("499"), Decimal("4990")),
            "pro": ("Pro", Decimal("899"), Decimal("8990")),
        }
        plans = {}
        for slug, (name, monthly, yearly) in defaults.items():
            plan, _ = Plan.objects.get_or_create(slug=slug, defaults={"name": name, "price_monthly": monthly, "price_yearly": yearly, "is_active": True, "is_public": True})
            plans[slug] = plan
        return plans

    def _mrr(self, subscription):
        if subscription.billing_interval == "yearly":
            return (subscription.plan.price_yearly or Decimal("0")) / Decimal("12")
        return subscription.plan.price_monthly or Decimal("0")

    def _event(self, organization, subscription, plan, key, event_type, effective_at, interval, mrr_delta, notes):
        event = RevenueEvent.objects.filter(metadata__demo_key=key).first()
        values = {"organization": organization, "subscription": subscription, "plan": plan, "event_type": event_type, "billing_interval": interval, "amount": abs(mrr_delta), "currency": plan.currency, "mrr_delta": mrr_delta, "arr_delta": mrr_delta * Decimal("12"), "effective_at": effective_at, "notes": notes, "metadata": {"demo_key": key, "source": "seed_backoffice_demo"}}
        if event:
            for field, value in values.items():
                setattr(event, field, value)
            event.save()
        else:
            RevenueEvent.objects.create(**values)

    def _activity(self, account, opportunity, contact, key, activity_type, subject, body, created_at, due_at=None):
        activity = SalesActivity.objects.filter(metadata__demo_key=key).first()
        values = {"account": account, "opportunity": opportunity, "contact": contact, "activity_type": activity_type, "subject": subject, "body": body, "due_at": due_at, "completed_at": created_at if activity_type != SalesActivity.TYPE_TASK else None, "metadata": {"demo_key": key, "source": "seed_backoffice_demo"}}
        if activity:
            for field, value in values.items():
                setattr(activity, field, value)
            activity.save()
        else:
            SalesActivity.objects.create(**values)
