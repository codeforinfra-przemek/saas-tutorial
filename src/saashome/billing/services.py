import calendar

from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
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
    promoted_types = {FranchisePromotion.TYPE_FEATURED, FranchisePromotion.TYPE_SEARCH_BOOST}

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
        franchise.has_verified_badge = (
            FranchisePromotion.TYPE_VERIFIED_BADGE in promotion_types
        )
        franchise.display_promoted = franchise.is_promoted or franchise.is_featured or franchise.has_premium_promotion
        franchise.display_verified = franchise.is_verified or franchise.has_verified_badge
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
            -float(franchise.rank_score or 0),
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
    change_request = FranchiseSubscriptionRequest.objects.select_for_update().select_related(
        "franchise",
        "requested_plan",
    ).get(pk=change_request.pk)
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
