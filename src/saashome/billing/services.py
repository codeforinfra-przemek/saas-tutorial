import calendar
from datetime import datetime, timezone as datetime_timezone

import stripe
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from franchises.models import Franchise

from .models import (
    BillingCustomer,
    FranchisePromotion,
    FranchiseSubscription,
    FranchiseSubscriptionRequest,
    OrganizationSubscription,
    Plan,
)


ACTIVE_SUBSCRIPTION_STATUSES = (
    OrganizationSubscription.STATUS_ACTIVE,
    OrganizationSubscription.STATUS_TRIAL,
)


def configure_stripe():
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("Brak STRIPE_SECRET_KEY w konfiguracji środowiska.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def _stripe_value(value, key, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _stripe_metadata(value):
    metadata = _stripe_value(value, "metadata", {}) or {}
    return dict(metadata)


def timestamp_to_datetime(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)


def map_stripe_status_to_internal_status(stripe_status):
    status_map = {
        "trialing": FranchiseSubscription.STATUS_ACTIVE,
        "active": FranchiseSubscription.STATUS_ACTIVE,
        "past_due": FranchiseSubscription.STATUS_PAST_DUE,
        "unpaid": FranchiseSubscription.STATUS_PAST_DUE,
        "incomplete": FranchiseSubscription.STATUS_PAST_DUE,
        "paused": FranchiseSubscription.STATUS_PAST_DUE,
        "canceled": FranchiseSubscription.STATUS_CANCELLED,
        "incomplete_expired": FranchiseSubscription.STATUS_EXPIRED,
    }
    return status_map.get(stripe_status, FranchiseSubscription.STATUS_PENDING)


def get_plan_by_stripe_price_id(price_id):
    if not price_id:
        return None
    return Plan.objects.filter(
        Q(stripe_price_monthly_id=price_id) | Q(stripe_price_yearly_id=price_id),
    ).first()


def get_or_create_billing_customer(organization, user=None):
    try:
        return organization.billing_customer
    except BillingCustomer.DoesNotExist:
        pass

    stripe_client = configure_stripe()
    email = organization.billing_email or organization.contact_email or getattr(user, "email", "")
    customer = stripe_client.Customer.create(
        email=email or None,
        name=organization.name,
        metadata={
            "organization_id": str(organization.pk),
            "organization_name": organization.name,
        },
        idempotency_key=f"saashome-organization-{organization.pk}",
    )
    return BillingCustomer.objects.create(
        organization=organization,
        stripe_customer_id=customer.id,
        email=email,
    )


def create_checkout_session(franchise, plan, user, billing_interval, request):
    if billing_interval not in {
        FranchiseSubscription.INTERVAL_MONTHLY,
        FranchiseSubscription.INTERVAL_YEARLY,
    }:
        raise ValidationError("Nieprawidłowy okres rozliczeniowy.")
    if not franchise.organization_id:
        raise ValidationError("Franczyza nie jest przypisana do organizacji.")

    price_id = (
        plan.stripe_price_monthly_id
        if billing_interval == FranchiseSubscription.INTERVAL_MONTHLY
        else plan.stripe_price_yearly_id
    )
    if not price_id:
        raise ValidationError("Ten wariant planu nie ma jeszcze skonfigurowanej ceny Stripe.")

    existing = FranchiseSubscription.objects.filter(franchise=franchise).first()
    if existing and existing.stripe_subscription_id and existing.status in {
        FranchiseSubscription.STATUS_ACTIVE,
        FranchiseSubscription.STATUS_PAST_DUE,
        FranchiseSubscription.STATUS_PENDING,
    }:
        raise ValidationError("Ta franczyza ma już subskrypcję Stripe. Zmień ją w panelu rozliczeń.")

    stripe_client = configure_stripe()
    billing_customer = get_or_create_billing_customer(franchise.organization, user)
    success_url = settings.BILLING_SUCCESS_URL or request.build_absolute_uri(
        f"{reverse('billing:success')}?session_id={{CHECKOUT_SESSION_ID}}"
    )
    cancel_url = settings.BILLING_CANCEL_URL or request.build_absolute_uri(
        reverse("billing:vendor_pricing")
    )
    metadata = {
        "organization_id": str(franchise.organization_id),
        "franchise_id": str(franchise.pk),
        "plan_id": str(plan.pk),
        "billing_interval": billing_interval,
        "user_id": str(user.pk),
    }
    session = stripe_client.checkout.Session.create(
        mode="subscription",
        customer=billing_customer.stripe_customer_id,
        client_reference_id=str(franchise.pk),
        line_items=[{"price": price_id, "quantity": 1}],
        metadata=metadata,
        subscription_data={"metadata": metadata},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def create_customer_portal_session(organization, request):
    try:
        billing_customer = organization.billing_customer
    except BillingCustomer.DoesNotExist as exc:
        raise ValidationError("Ta organizacja nie ma jeszcze profilu rozliczeniowego Stripe.") from exc

    stripe_client = configure_stripe()
    return_url = request.build_absolute_uri(reverse("billing:vendor_billing"))
    session = stripe_client.billing_portal.Session.create(
        customer=billing_customer.stripe_customer_id,
        return_url=return_url,
    )
    return session.url


@transaction.atomic
def sync_subscription_from_stripe(stripe_subscription):
    stripe_customer = _stripe_value(stripe_subscription, "customer", "")
    if not isinstance(stripe_customer, str):
        stripe_customer = _stripe_value(stripe_customer, "id", "")
    billing_customer = BillingCustomer.objects.select_related("organization").get(
        stripe_customer_id=stripe_customer
    )

    items = _stripe_value(_stripe_value(stripe_subscription, "items", {}), "data", []) or []
    if not items:
        raise ValidationError("Subskrypcja Stripe nie zawiera pozycji planu.")
    first_item = items[0]
    price = _stripe_value(first_item, "price", {})
    price_id = price if isinstance(price, str) else _stripe_value(price, "id", "")
    plan = get_plan_by_stripe_price_id(price_id)
    if not plan:
        raise ValidationError(f"Nie znaleziono planu dla ceny Stripe {price_id}.")

    metadata = _stripe_metadata(stripe_subscription)
    franchise_id = metadata.get("franchise_id")
    if not franchise_id:
        raise ValidationError("Subskrypcja Stripe nie zawiera franchise_id.")
    franchise = Franchise.objects.select_related("organization").get(pk=franchise_id)
    if franchise.organization_id != billing_customer.organization_id:
        raise ValidationError("Franczyza i klient Stripe należą do różnych organizacji.")

    subscription_id = _stripe_value(stripe_subscription, "id", "")
    duplicate = FranchiseSubscription.objects.exclude(franchise=franchise).filter(
        stripe_subscription_id=subscription_id
    )
    if duplicate.exists():
        raise ValidationError("Identyfikator subskrypcji Stripe jest już przypisany do innej franczyzy.")

    stripe_status = _stripe_value(stripe_subscription, "status", "")
    internal_status = map_stripe_status_to_internal_status(stripe_status)
    period_start = _stripe_value(stripe_subscription, "current_period_start") or _stripe_value(
        first_item, "current_period_start"
    )
    period_end = _stripe_value(stripe_subscription, "current_period_end") or _stripe_value(
        first_item, "current_period_end"
    )
    current_period_start = timestamp_to_datetime(period_start)
    current_period_end = timestamp_to_datetime(period_end)
    billing_interval = metadata.get("billing_interval", "")
    if not billing_interval:
        recurring = _stripe_value(price, "recurring", {})
        interval = _stripe_value(recurring, "interval", "")
        billing_interval = "yearly" if interval == "year" else "monthly" if interval == "month" else ""

    if internal_status == FranchiseSubscription.STATUS_ACTIVE:
        payment_status = FranchiseSubscription.PAYMENT_PAID
    elif internal_status == FranchiseSubscription.STATUS_PAST_DUE:
        payment_status = FranchiseSubscription.PAYMENT_OVERDUE
    else:
        payment_status = FranchiseSubscription.PAYMENT_NOT_REQUIRED

    defaults = {
        "plan": plan,
        "status": internal_status,
        "starts_at": current_period_start or timestamp_to_datetime(
            _stripe_value(stripe_subscription, "created")
        ),
        "ends_at": current_period_end,
        "cancel_at_period_end": bool(_stripe_value(stripe_subscription, "cancel_at_period_end", False)),
        "billing_interval": billing_interval,
        "stripe_customer_id": stripe_customer,
        "stripe_subscription_id": subscription_id,
        "stripe_price_id": price_id,
        "stripe_status": stripe_status,
        "current_period_start": current_period_start,
        "current_period_end": current_period_end,
        "manual_payment_status": payment_status,
    }
    subscription, _ = FranchiseSubscription.objects.update_or_create(
        franchise=franchise,
        defaults=defaults,
    )
    return subscription


def process_stripe_event(event):
    event_type = _stripe_value(event, "type", "")
    event_data = _stripe_value(_stripe_value(event, "data", {}), "object", {})
    if event_type == "checkout.session.completed":
        subscription_id = _stripe_value(event_data, "subscription")
        if subscription_id:
            if not isinstance(subscription_id, str):
                subscription_id = _stripe_value(subscription_id, "id")
            stripe_subscription = configure_stripe().Subscription.retrieve(subscription_id)
            return sync_subscription_from_stripe(stripe_subscription)
        return None
    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        return sync_subscription_from_stripe(event_data)
    return None


def get_active_subscription(organization):
    if not organization:
        return None

    now = timezone.now()
    try:
        return (
            OrganizationSubscription.objects.select_related("plan")
            .filter(
                organization=organization,
                status__in=ACTIVE_SUBSCRIPTION_STATUSES,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
            .order_by("-starts_at")
            .first()
        )
    except (OperationalError, ProgrammingError):
        return None


def get_organization_plan(organization):
    subscription = get_active_subscription(organization)
    if subscription:
        return subscription.plan
    try:
        return Plan.objects.filter(slug="free", is_active=True).first()
    except (OperationalError, ProgrammingError):
        return None


def organization_has_feature(organization, feature_name):
    plan = get_organization_plan(organization)
    if not plan or not hasattr(plan, feature_name):
        return False
    return bool(getattr(plan, feature_name))


def get_active_franchise_subscription(franchise):
    if not franchise:
        return None

    now = timezone.now()
    try:
        return (
            FranchiseSubscription.objects.select_related("plan", "franchise__organization")
            .filter(
                franchise=franchise,
                status=FranchiseSubscription.STATUS_ACTIVE,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
            .first()
        )
    except (OperationalError, ProgrammingError):
        return None


def get_active_franchise_subscription_map(franchises):
    franchise_ids = [franchise.id for franchise in franchises]
    if not franchise_ids:
        return {}
    now = timezone.now()
    try:
        subscriptions = (
            FranchiseSubscription.objects.select_related("plan")
            .filter(
                franchise_id__in=franchise_ids,
                status=FranchiseSubscription.STATUS_ACTIVE,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        )
        return {subscription.franchise_id: subscription for subscription in subscriptions}
    except (OperationalError, ProgrammingError):
        return {}


def get_franchise_plan(franchise):
    if not franchise:
        return None

    subscription = get_active_franchise_subscription(franchise)
    if subscription:
        return subscription.plan

    try:
        has_explicit_subscription = FranchiseSubscription.objects.filter(franchise=franchise).exists()
    except (OperationalError, ProgrammingError):
        has_explicit_subscription = False
    if has_explicit_subscription:
        try:
            return Plan.objects.filter(slug="free", is_active=True).first()
        except (OperationalError, ProgrammingError):
            return None

    return get_organization_plan(getattr(franchise, "organization", None))


def franchise_has_feature(franchise, feature_name):
    plan = get_franchise_plan(franchise)
    return bool(plan and hasattr(plan, feature_name) and getattr(plan, feature_name))


def get_franchise_plan_map(franchises):
    franchise_list = list(franchises)
    if not franchise_list:
        return {}
    franchise_ids = [franchise.id for franchise in franchise_list]
    active_map = get_active_franchise_subscription_map(franchise_list)
    try:
        explicit_ids = set(
            FranchiseSubscription.objects.filter(franchise_id__in=franchise_ids).values_list(
                "franchise_id",
                flat=True,
            )
        )
        free_plan = Plan.objects.filter(slug="free", is_active=True).first()
    except (OperationalError, ProgrammingError):
        explicit_ids = set()
        free_plan = None

    organization_ids = {
        franchise.organization_id
        for franchise in franchise_list
        if franchise.id not in explicit_ids and franchise.organization_id
    }
    organization_plan_map = {}
    if organization_ids:
        now = timezone.now()
        try:
            legacy_subscriptions = (
                OrganizationSubscription.objects.select_related("plan")
                .filter(
                    organization_id__in=organization_ids,
                    status__in=ACTIVE_SUBSCRIPTION_STATUSES,
                    starts_at__lte=now,
                )
                .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
                .order_by("organization_id", "-starts_at")
            )
            for subscription in legacy_subscriptions:
                organization_plan_map.setdefault(subscription.organization_id, subscription.plan)
        except (OperationalError, ProgrammingError):
            pass

    return {
        franchise.id: (
            active_map[franchise.id].plan
            if franchise.id in active_map
            else free_plan
            if franchise.id in explicit_ids
            else organization_plan_map.get(franchise.organization_id, free_plan)
        )
        for franchise in franchise_list
    }


def get_active_promotions_for_franchise(franchise):
    if not franchise:
        return FranchisePromotion.objects.none()

    now = timezone.now()
    try:
        return (
            FranchisePromotion.objects.filter(
                franchise=franchise,
                status=FranchisePromotion.STATUS_ACTIVE,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
            .order_by("-priority", "-starts_at")
        )
    except (OperationalError, ProgrammingError):
        return FranchisePromotion.objects.none()


def franchise_has_active_promotion(franchise, promotion_type):
    return get_active_promotions_for_franchise(franchise).filter(promotion_type=promotion_type).exists()


def get_active_promotion_map(franchises):
    franchise_ids = [franchise.id for franchise in franchises]
    if not franchise_ids:
        return {}

    now = timezone.now()
    try:
        promotions = list(
            FranchisePromotion.objects.filter(
                franchise_id__in=franchise_ids,
                status=FranchisePromotion.STATUS_ACTIVE,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        )
    except (OperationalError, ProgrammingError):
        return {}

    promotion_map = {}
    for promotion in promotions:
        promotion_map.setdefault(promotion.franchise_id, []).append(promotion)
    return promotion_map


def apply_promotion_flags(franchises):
    franchise_list = list(franchises)
    promotion_map = get_active_promotion_map(franchise_list)
    plan_map = get_franchise_plan_map(franchise_list)
    promoted_types = {
        FranchisePromotion.TYPE_FEATURED,
        FranchisePromotion.TYPE_SEARCH_BOOST,
        # Historical paid "verified" products are promotion only. They must
        # never imply independent data verification.
        FranchisePromotion.TYPE_VERIFIED_BADGE,
    }

    for franchise in franchise_list:
        promotions = promotion_map.get(franchise.id, [])
        promotion_types = {promotion.promotion_type for promotion in promotions}
        plan = plan_map.get(franchise.id)
        plan_priority = plan.sort_order if plan and plan.can_be_promoted else 0
        franchise.active_promotions = promotions
        franchise.subscription_plan = plan
        franchise.has_premium_promotion = bool(promoted_types & promotion_types) or bool(
            plan and plan.can_be_promoted
        )
        franchise.has_legacy_verified_promotion = (
            FranchisePromotion.TYPE_VERIFIED_BADGE in promotion_types
        )
        franchise.display_promoted = franchise.is_promoted or franchise.is_featured or franchise.has_premium_promotion
        franchise.display_data_verified = bool(
            franchise.is_verified
            and franchise.data_status != Franchise.DATA_STATUS_DEMO
        )
        # Compatibility for older templates and integrations. Paid promotion
        # is intentionally excluded from this value.
        franchise.display_verified = franchise.display_data_verified
        franchise.display_research_reviewed = (
            franchise.data_status == Franchise.DATA_STATUS_RESEARCH_REVIEWED
        )
        franchise.display_research_with_gaps = (
            franchise.data_status == Franchise.DATA_STATUS_RESEARCH_WITH_GAPS
        )
        franchise.display_vendor_data = (
            franchise.data_status == Franchise.DATA_STATUS_VENDOR
        )
        franchise.display_featured = franchise.is_featured or bool(
            plan and (plan.can_feature_in_category or plan.can_feature_on_homepage)
        )
        franchise.promotion_priority = max(
            [promotion.priority for promotion in promotions] + [plan_priority],
            default=0,
        )

    return sorted(
        franchise_list,
        key=lambda franchise: (
            not franchise.display_promoted,
            -franchise.promotion_priority,
            -float(
                franchise.rank_score
                if franchise.data_status != Franchise.DATA_STATUS_DEMO
                else 0
            ),
            franchise.name.lower(),
        ),
    )


def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def create_subscription_request(franchise, user, request_type, requested_plan=None, duration_months=1, notes=""):
    if FranchiseSubscriptionRequest.objects.filter(
        franchise=franchise,
        status=FranchiseSubscriptionRequest.STATUS_PENDING,
    ).exists():
        raise ValidationError("Ta franczyza ma już oczekujące żądanie zmiany subskrypcji.")

    subscription = FranchiseSubscription.objects.filter(franchise=franchise).first()
    return FranchiseSubscriptionRequest.objects.create(
        franchise=franchise,
        subscription=subscription,
        request_type=request_type,
        requested_plan=requested_plan,
        duration_months=duration_months,
        requested_by=user,
        vendor_notes=notes,
    )


@transaction.atomic
def approve_subscription_request(change_request, reviewer):
    change_request = (
        FranchiseSubscriptionRequest.objects.select_for_update(of=("self",))
        .select_related("franchise", "requested_plan")
        .get(pk=change_request.pk)
    )
    if change_request.status != FranchiseSubscriptionRequest.STATUS_PENDING:
        raise ValidationError("To żądanie zostało już rozpatrzone.")

    now = timezone.now()
    subscription = FranchiseSubscription.objects.select_for_update().filter(
        franchise=change_request.franchise,
    ).first()

    if change_request.request_type == FranchiseSubscriptionRequest.TYPE_CANCEL:
        if not subscription:
            raise ValidationError("Franczyza nie ma subskrypcji do anulowania.")
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
    else:
        if not change_request.requested_plan:
            raise ValidationError("Wybierz plan dla tego żądania.")
        if not subscription:
            subscription = FranchiseSubscription(franchise=change_request.franchise)

        if change_request.request_type == FranchiseSubscriptionRequest.TYPE_EXTEND:
            base_date = subscription.ends_at if subscription.ends_at and subscription.ends_at > now else now
            subscription.ends_at = add_months(base_date, change_request.duration_months)
        elif change_request.request_type == FranchiseSubscriptionRequest.TYPE_START:
            subscription.starts_at = now
            subscription.ends_at = add_months(now, change_request.duration_months)
        elif change_request.request_type == FranchiseSubscriptionRequest.TYPE_CHANGE_PLAN:
            if not subscription.starts_at or not subscription.ends_at or subscription.ends_at <= now:
                subscription.starts_at = now
                subscription.ends_at = add_months(now, change_request.duration_months)

        subscription.plan = change_request.requested_plan
        subscription.status = FranchiseSubscription.STATUS_ACTIVE
        subscription.cancel_at_period_end = False
        subscription.manual_payment_status = FranchiseSubscription.PAYMENT_PAID
        subscription.requested_by = change_request.requested_by
        subscription.save()

    change_request.subscription = subscription
    change_request.status = FranchiseSubscriptionRequest.STATUS_APPROVED
    change_request.reviewed_by = reviewer
    change_request.reviewed_at = now
    change_request.save(
        update_fields=["subscription", "status", "reviewed_by", "reviewed_at", "updated_at"]
    )
    return subscription


def reject_subscription_request(change_request, reviewer, notes=""):
    if change_request.status != FranchiseSubscriptionRequest.STATUS_PENDING:
        raise ValidationError("To żądanie zostało już rozpatrzone.")
    change_request.status = FranchiseSubscriptionRequest.STATUS_REJECTED
    change_request.reviewed_by = reviewer
    change_request.reviewed_at = timezone.now()
    change_request.admin_notes = notes
    change_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "admin_notes", "updated_at"]
    )
    return change_request
