import os
from datetime import timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Organization, OrganizationMembership, UserProfile
from billing.models import FranchiseSubscription, Plan
from franchises.models import Franchise, FranchiseCategory
from leads.models import Lead, LeadActivity
from visits.models import Visit, VisitEvent


class Command(BaseCommand):
    help = "Create idempotent local users, organizations and billing examples."

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_USER_PASSWORD", "DemoTest123!")
        call_command("seed_plans", verbosity=0)

        users = {
            "viewer": self._user("demo.viewer", "demo.viewer@example.com", password),
            "owner": self._user("demo.owner", "demo.owner@example.com", password, vendor=True),
            "admin": self._user("demo.admin", "demo.admin@example.com", password, vendor=True),
            "member": self._user("demo.member", "demo.member@example.com", password, vendor=True),
            "other_owner": self._user(
                "demo.other.owner",
                "demo.other.owner@example.com",
                password,
                vendor=True,
            ),
            "staff": self._user(
                "demo.staff",
                "demo.staff@example.com",
                password,
                is_staff=True,
            ),
        }
        alpha, _ = Organization.objects.update_or_create(
            slug="demo-alpha-group",
            defaults={
                "name": "Demo Alpha Group",
                "contact_email": users["owner"].email,
                "billing_email": users["owner"].email,
                "status": Organization.STATUS_ACTIVE,
            },
        )
        beta, _ = Organization.objects.update_or_create(
            slug="demo-beta-holdings",
            defaults={
                "name": "Demo Beta Holdings",
                "contact_email": users["other_owner"].email,
                "billing_email": users["other_owner"].email,
                "status": Organization.STATUS_ACTIVE,
            },
        )
        self._membership(alpha, users["owner"], OrganizationMembership.ROLE_OWNER)
        self._membership(alpha, users["admin"], OrganizationMembership.ROLE_ADMIN)
        self._membership(alpha, users["member"], OrganizationMembership.ROLE_MEMBER)
        self._membership(beta, users["other_owner"], OrganizationMembership.ROLE_OWNER)

        category, _ = FranchiseCategory.objects.update_or_create(
            slug="demo-services",
            defaults={"name": "Usługi demonstracyjne", "is_active": True, "sort_order": 900},
        )
        coffee = self._franchise(
            "demo-coffee-lab",
            "Demo Coffee Lab",
            alpha,
            category,
            Decimal("180000"),
        )
        fitness = self._franchise(
            "demo-fitness-hub",
            "Demo Fitness Hub",
            alpha,
            category,
            Decimal("420000"),
        )
        legal = self._franchise(
            "demo-legal-point",
            "Demo Legal Point",
            beta,
            category,
            Decimal("95000"),
        )

        now = timezone.now()
        for franchise, plan_slug, interval, days in (
            (coffee, "basic", FranchiseSubscription.INTERVAL_MONTHLY, 30),
            (fitness, "growth", FranchiseSubscription.INTERVAL_YEARLY, 365),
            (legal, "pro", FranchiseSubscription.INTERVAL_MONTHLY, 30),
        ):
            FranchiseSubscription.objects.update_or_create(
                franchise=franchise,
                defaults={
                    "plan": Plan.objects.get(slug=plan_slug),
                    "status": FranchiseSubscription.STATUS_ACTIVE,
                    "starts_at": now,
                    "ends_at": now + timedelta(days=days),
                    "billing_interval": interval,
                    "manual_payment_status": FranchiseSubscription.PAYMENT_PAID,
                    "requested_by": users["owner"] if franchise.organization_id == alpha.pk else users["other_owner"],
                },
            )
            self._activity(franchise)

        self.stdout.write(self.style.SUCCESS("Demo billing data is ready."))
        self.stdout.write(f"Password for all demo users: {password}")
        for role, user in users.items():
            self.stdout.write(f"  {role:11} {user.email}")

    def _user(self, username, email, password, vendor=False, is_staff=False):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(username=username, defaults={"email": email})
        user.email = email
        user.is_active = True
        user.is_staff = is_staff
        user.set_password(password)
        user.save()
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.user_type = UserProfile.USER_TYPE_VENDOR if vendor else UserProfile.USER_TYPE_USER
        profile.email_verified = True
        profile.save(update_fields=["user_type", "email_verified", "updated_at"])
        return user

    def _membership(self, organization, user, role):
        OrganizationMembership.objects.update_or_create(
            organization=organization,
            user=user,
            defaults={"role": role, "is_active": True},
        )

    def _franchise(self, slug, name, organization, category, investment):
        franchise, _ = Franchise.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "organization": organization,
                "category": category,
                "short_description": "Demonstracyjny profil do testowania planów i izolacji danych vendora.",
                "description": "Dane demonstracyjne. Profil pozwala sprawdzić limity planu, leady, analitykę oraz rozliczenia.",
                "min_investment": investment,
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "training_provided": True,
                "is_active": True,
            },
        )
        return franchise

    def _activity(self, franchise):
        for index in range(1, 7):
            session_key = f"demo-billing-{franchise.slug}-{index}"
            visit, _ = Visit.objects.get_or_create(
                session_key=session_key,
                franchise=franchise,
                defaults={
                    "path": franchise.get_absolute_url(),
                    "full_path": franchise.get_absolute_url(),
                    "page_type": Visit.PAGE_TYPE_FRANCHISE_DETAIL,
                    "utm_source": "demo",
                    "user_agent": "SaaS Home demo seed",
                },
            )
            VisitEvent.objects.get_or_create(
                visit=visit,
                event_type=VisitEvent.EVENT_PAGE_VIEW,
                defaults={"metadata": {"seed": True}},
            )
        lead, _ = Lead.objects.get_or_create(
            franchise=franchise,
            session_key=f"demo-lead-{franchise.slug}",
            defaults={
                "name": "Anna Testowa",
                "email": f"lead+{franchise.slug}@example.com",
                "phone": "+48 500 600 700",
                "city": "Warszawa",
                "investment_budget": franchise.min_investment,
                "privacy_consent": True,
                "utm_source": "demo",
            },
        )
        LeadActivity.objects.get_or_create(
            lead=lead,
            activity_type=LeadActivity.TYPE_LEAD_CREATED,
            defaults={"metadata": {"seed": True}},
        )
