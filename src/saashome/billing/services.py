from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.utils import timezone

from .models import FranchisePromotion, OrganizationSubscription, Plan


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
    promoted_types = {FranchisePromotion.TYPE_FEATURED, FranchisePromotion.TYPE_SEARCH_BOOST}

    for franchise in franchise_list:
        promotions = promotion_map.get(franchise.id, [])
        promotion_types = {promotion.promotion_type for promotion in promotions}
        franchise.active_promotions = promotions
        franchise.has_premium_promotion = bool(promoted_types & promotion_types)
        franchise.has_verified_badge = FranchisePromotion.TYPE_VERIFIED_BADGE in promotion_types
        franchise.display_promoted = franchise.is_promoted or franchise.is_featured or franchise.has_premium_promotion
        franchise.display_verified = franchise.is_verified or franchise.has_verified_badge
        franchise.promotion_priority = max([promotion.priority for promotion in promotions], default=0)

    return sorted(
        franchise_list,
        key=lambda franchise: (
            not franchise.display_promoted,
            -franchise.promotion_priority,
            -float(franchise.rank_score or 0),
            franchise.name.lower(),
        ),
    )
