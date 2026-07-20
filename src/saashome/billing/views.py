import json

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import OperationalError, ProgrammingError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import (
    can_manage_franchise_billing,
    has_active_vendor_membership,
    staff_required,
    vendor_required,
)
from accounts.services import get_user_franchises, get_user_organizations

from .forms import FranchiseSubscriptionActionForm, InvestorServiceRequestForm, SubscriptionReviewForm
from .models import (
    FranchiseSubscription,
    FranchiseSubscriptionRequest,
    InvestorServiceRequest,
    Plan,
    StripeWebhookEvent,
)
from .services import (
    approve_subscription_request,
    create_checkout_session,
    create_customer_portal_session,
    create_subscription_request,
    get_active_franchise_subscription,
    get_active_franchise_subscription_map,
    process_stripe_event,
    reject_subscription_request,
)


PAID_PLAN_SLUGS = ("basic", "growth", "pro")


SPECIALIST_AREAS = (
    ("location", "Lokal i analiza lokalizacji"),
    ("legal", "Prawo i umowa franczyzowa"),
    ("design_build", "Projekt, adaptacja i budowa lokalu"),
    ("finance", "Finansowanie inwestycji"),
    ("operations", "Operacje i otwarcie placówki"),
)


def get_subscription_mode(franchise, subscription):
    if not subscription:
        return "Free"
    if subscription.stripe_subscription_id:
        return "Stripe"
    if franchise.slug.startswith("demo-"):
        return "Demo"
    return "Manualna"


def investor_services_view(request):
    initial = {"service_type": request.GET.get("service", InvestorServiceRequest.SERVICE_LOCATION_REPORT)}
    specialist_area = request.GET.get("specialist", "")
    if specialist_area:
        initial.update(
            {
                "service_type": InvestorServiceRequest.SERVICE_SPECIALIST_MATCH,
                "specialist_area": specialist_area,
            }
        )
    if request.user.is_authenticated:
        initial.update(
            {
                "name": request.user.get_full_name() or request.user.username,
                "email": request.user.email,
            }
        )

    form = InvestorServiceRequestForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        service_request = form.save(commit=False)
        if request.user.is_authenticated:
            service_request.user = request.user
        service_request.save()
        messages.success(
            request,
            "Zapisaliśmy zgłoszenie. Skontaktujemy się z Tobą, aby potwierdzić zakres i sposób płatności.",
        )
        return redirect("billing:investor_services")

    return render(
        request,
        "billing/investor_services.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Usługi dla inwestora",
            "active_page": "investor_services",
            "form": form,
            "specialist_areas": SPECIALIST_AREAS,
        },
    )


def pricing_view(request):
    if not has_active_vendor_membership(request.user):
        return investor_services_view(request)
    return vendor_pricing_view(request)


@vendor_required
def vendor_pricing_view(request):
    try:
        plans = list(
            Plan.objects.filter(is_active=True, is_public=True, slug__in=PAID_PLAN_SLUGS)
            .order_by("sort_order", "price_monthly", "name")
        )
    except (OperationalError, ProgrammingError):
        plans = []
    franchises = list(get_user_franchises(request.user))
    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Pricing",
        "active_page": "pricing",
        "plans": plans,
        "franchises": franchises,
        "manageable_franchises": [
            franchise
            for franchise in franchises
            if can_manage_franchise_billing(request.user, franchise)
        ],
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    return render(request, "billing/pricing.html", context)


@vendor_required
def franchise_subscription_list_view(request):
    franchises = list(get_user_franchises(request.user))
    active_subscriptions = get_active_franchise_subscription_map(franchises)
    subscriptions = {
        subscription.franchise_id: subscription
        for subscription in FranchiseSubscription.objects.filter(franchise__in=franchises).select_related("plan")
    }
    pending_requests = {
        change_request.franchise_id: change_request
        for change_request in FranchiseSubscriptionRequest.objects.filter(
            franchise__in=franchises,
            status=FranchiseSubscriptionRequest.STATUS_PENDING,
        ).select_related("requested_plan")
    }
    rows = []
    for franchise in franchises:
        rows.append(
            {
                "franchise": franchise,
                "subscription": subscriptions.get(franchise.id),
                "active_subscription": active_subscriptions.get(franchise.id),
                "pending_request": pending_requests.get(franchise.id),
                "can_manage": can_manage_franchise_billing(request.user, franchise),
                "billing_mode": get_subscription_mode(franchise, subscriptions.get(franchise.id)),
            }
        )
    return render(
        request,
        "billing/subscriptions/list.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Subskrypcje franczyz",
            "active_page": "subscriptions",
            "rows": rows,
        },
    )


@vendor_required
def franchise_subscription_detail_view(request, slug):
    franchise = get_object_or_404(get_user_franchises(request.user), slug=slug)
    subscription = FranchiseSubscription.objects.filter(franchise=franchise).select_related("plan").first()
    active_subscription = get_active_franchise_subscription(franchise)
    pending_request = FranchiseSubscriptionRequest.objects.filter(
        franchise=franchise,
        status=FranchiseSubscriptionRequest.STATUS_PENDING,
    ).select_related("requested_plan").first()
    history = FranchiseSubscriptionRequest.objects.filter(franchise=franchise).select_related(
        "requested_plan",
        "requested_by",
        "reviewed_by",
    )[:20]
    plans = Plan.objects.filter(
        is_active=True,
        is_public=True,
        slug__in=PAID_PLAN_SLUGS,
    ).order_by("sort_order")
    return render(
        request,
        "billing/subscriptions/detail.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": f"Subskrypcja: {franchise.name}",
            "active_page": "subscriptions",
            "franchise": franchise,
            "subscription": subscription,
            "active_subscription": active_subscription,
            "pending_request": pending_request,
            "history": history,
            "plans": plans,
            "duration_choices": FranchiseSubscriptionRequest.DURATION_CHOICES,
            "can_manage": can_manage_franchise_billing(request.user, franchise),
            "billing_mode": get_subscription_mode(franchise, subscription),
        },
    )


@vendor_required
@require_POST
def franchise_subscription_request_view(request, slug, action):
    franchise = get_object_or_404(get_user_franchises(request.user), slug=slug)
    if not can_manage_franchise_billing(request.user, franchise):
        raise PermissionDenied

    action_map = {
        "start": FranchiseSubscriptionRequest.TYPE_START,
        "extend": FranchiseSubscriptionRequest.TYPE_EXTEND,
        "change": FranchiseSubscriptionRequest.TYPE_CHANGE_PLAN,
        "cancel": FranchiseSubscriptionRequest.TYPE_CANCEL,
    }
    request_type = action_map.get(action)
    if not request_type:
        raise PermissionDenied

    active_subscription = get_active_franchise_subscription(franchise)
    plans = Plan.objects.filter(is_active=True, slug__in=PAID_PLAN_SLUGS)
    require_plan = request_type in (
        FranchiseSubscriptionRequest.TYPE_START,
        FranchiseSubscriptionRequest.TYPE_CHANGE_PLAN,
    )
    require_duration = request_type in (
        FranchiseSubscriptionRequest.TYPE_START,
        FranchiseSubscriptionRequest.TYPE_EXTEND,
    )
    form = FranchiseSubscriptionActionForm(
        request.POST,
        plans=plans,
        require_plan=require_plan,
        require_duration=require_duration,
    )
    if not form.is_valid():
        messages.error(request, "Uzupełnij poprawnie dane zmiany subskrypcji.")
        return redirect("billing:subscription_detail", slug=slug)

    if request_type == FranchiseSubscriptionRequest.TYPE_START and active_subscription:
        messages.error(request, "Ta franczyza ma już aktywną subskrypcję.")
        return redirect("billing:subscription_detail", slug=slug)
    if request_type != FranchiseSubscriptionRequest.TYPE_START and not active_subscription:
        messages.error(request, "Ta franczyza nie ma aktywnej subskrypcji.")
        return redirect("billing:subscription_detail", slug=slug)

    requested_plan = form.cleaned_data.get("plan")
    if request_type in (FranchiseSubscriptionRequest.TYPE_EXTEND, FranchiseSubscriptionRequest.TYPE_CANCEL):
        requested_plan = active_subscription.plan if active_subscription else None

    try:
        create_subscription_request(
            franchise=franchise,
            user=request.user,
            request_type=request_type,
            requested_plan=requested_plan,
            duration_months=form.cleaned_data.get("duration_months") or 1,
            notes=form.cleaned_data.get("notes", ""),
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, "Żądanie zostało przekazane do zatwierdzenia i rozliczenia.")
    return redirect("billing:subscription_detail", slug=slug)


@staff_required
def subscription_request_manage_view(request):
    requests = FranchiseSubscriptionRequest.objects.select_related(
        "franchise",
        "franchise__organization",
        "requested_plan",
        "requested_by",
        "reviewed_by",
    )
    status = request.GET.get("status", FranchiseSubscriptionRequest.STATUS_PENDING)
    if status:
        requests = requests.filter(status=status)
    return render(
        request,
        "billing/subscriptions/manage_requests.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Obsługa subskrypcji",
            "active_page": "subscription_management",
            "requests": requests[:200],
            "status": status,
            "status_choices": FranchiseSubscriptionRequest.STATUS_CHOICES,
            "review_form": SubscriptionReviewForm(),
        },
    )


@staff_required
@require_POST
def subscription_request_review_view(request, pk, decision):
    change_request = get_object_or_404(FranchiseSubscriptionRequest, pk=pk)
    form = SubscriptionReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Nie udało się zapisać decyzji.")
        return redirect("billing:manage_requests")
    try:
        if decision == "approve":
            approve_subscription_request(change_request, request.user)
            messages.success(request, "Subskrypcja została zaktualizowana.")
        elif decision == "reject":
            reject_subscription_request(
                change_request,
                request.user,
                form.cleaned_data.get("admin_notes", ""),
            )
            messages.success(request, "Żądanie zostało odrzucone.")
        else:
            raise PermissionDenied
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("billing:manage_requests")


@vendor_required
@require_POST
def checkout_view(request, plan_slug=None):
    plan_slug = plan_slug or request.POST.get("plan_slug", "")
    plan = get_object_or_404(
        Plan,
        slug=plan_slug,
        is_active=True,
        is_public=True,
    )
    franchise = get_object_or_404(
        get_user_franchises(request.user),
        pk=request.POST.get("franchise_id"),
    )
    if not can_manage_franchise_billing(request.user, franchise):
        raise PermissionDenied

    billing_interval = request.POST.get(
        "billing_interval",
        FranchiseSubscription.INTERVAL_MONTHLY,
    )
    try:
        checkout_url = create_checkout_session(
            franchise=franchise,
            plan=plan,
            user=request.user,
            billing_interval=billing_interval,
            request=request,
        )
    except (ImproperlyConfigured, ValidationError, stripe.StripeError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else "Stripe nie mógł rozpocząć płatności. Spróbuj ponownie."
        messages.error(request, message)
        return redirect("billing:vendor_pricing")
    return redirect(checkout_url)


@login_required
def billing_success_view(request):
    return render(
        request,
        "billing/success.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Płatność przyjęta",
            "active_page": "subscriptions",
        },
    )


@vendor_required
def vendor_billing_view(request):
    organizations = list(get_user_organizations(request.user).prefetch_related("franchises"))
    franchises = list(get_user_franchises(request.user))
    subscriptions = {
        subscription.franchise_id: subscription
        for subscription in FranchiseSubscription.objects.filter(
            franchise__in=franchises
        ).select_related("plan", "franchise__organization")
    }
    rows = [
        {
            "franchise": franchise,
            "subscription": subscriptions.get(franchise.pk),
            "can_manage": can_manage_franchise_billing(request.user, franchise),
            "billing_mode": get_subscription_mode(franchise, subscriptions.get(franchise.pk)),
        }
        for franchise in franchises
    ]
    customer_organization_ids = {
        organization.pk
        for organization in organizations
        if hasattr(organization, "billing_customer")
    }
    return render(
        request,
        "billing/vendor_billing.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Rozliczenia",
            "active_page": "subscriptions",
            "organizations": organizations,
            "customer_organization_ids": customer_organization_ids,
            "rows": rows,
        },
    )


@vendor_required
@require_POST
def customer_portal_view(request):
    franchise = get_object_or_404(
        get_user_franchises(request.user),
        pk=request.POST.get("franchise_id"),
    )
    if not can_manage_franchise_billing(request.user, franchise):
        raise PermissionDenied
    try:
        portal_url = create_customer_portal_session(franchise.organization, request)
    except (ImproperlyConfigured, ValidationError, stripe.StripeError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else "Nie udało się otworzyć panelu Stripe. Spróbuj ponownie."
        messages.error(request, message)
        return redirect("billing:vendor_billing")
    return redirect(portal_url)


@csrf_exempt
@require_POST
def stripe_webhook_view(request):
    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=503)

    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            request.body,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponse(status=400)

    if hasattr(event, "to_dict_recursive"):
        event_payload = event.to_dict_recursive()
    elif isinstance(event, dict):
        event_payload = event
    else:
        event_payload = json.loads(request.body.decode("utf-8"))
    event_id = event_payload.get("id", "")
    event_type = event_payload.get("type", "")
    if not event_id or not event_type:
        return HttpResponse(status=400)

    try:
        with transaction.atomic():
            webhook_event, _ = StripeWebhookEvent.objects.select_for_update().get_or_create(
                stripe_event_id=event_id,
                defaults={
                    "event_type": event_type,
                    "payload": event_payload,
                },
            )
            if webhook_event.processed:
                return HttpResponse(status=200)
            webhook_event.event_type = event_type
            webhook_event.payload = event_payload
            webhook_event.processing_error = ""
            process_stripe_event(event_payload)
            webhook_event.processed = True
            webhook_event.processed_at = timezone.now()
            webhook_event.save(
                update_fields=[
                    "event_type",
                    "payload",
                    "processing_error",
                    "processed",
                    "processed_at",
                ]
            )
    except Exception as exc:
        StripeWebhookEvent.objects.update_or_create(
            stripe_event_id=event_id,
            defaults={
                "event_type": event_type,
                "payload": event_payload,
                "processed": False,
                "processing_error": str(exc)[:2000],
                "processed_at": None,
            },
        )
        return HttpResponse(status=500)
    return HttpResponse(status=200)
