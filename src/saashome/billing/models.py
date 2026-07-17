from django.conf import settings
from django.db import models


class Plan(models.Model):
    CURRENCY_PLN = "PLN"
    CURRENCY_EUR = "EUR"
    CURRENCY_USD = "USD"
    CURRENCY_CHOICES = (
        (CURRENCY_PLN, "PLN"),
        (CURRENCY_EUR, "EUR"),
        (CURRENCY_USD, "USD"),
    )

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=CURRENCY_PLN)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    stripe_product_id = models.CharField(max_length=255, blank=True)
    stripe_price_monthly_id = models.CharField(max_length=255, blank=True)
    stripe_price_yearly_id = models.CharField(max_length=255, blank=True)

    can_view_leads = models.BooleanField(default=False)
    can_view_analytics = models.BooleanField(default=False)
    can_show_website = models.BooleanField(default=False)
    can_show_documents = models.BooleanField(default=False)
    can_be_verified = models.BooleanField(default=False)
    can_be_promoted = models.BooleanField(default=False)
    can_receive_priority_leads = models.BooleanField(default=False)
    can_feature_in_category = models.BooleanField(default=False)
    can_feature_on_homepage = models.BooleanField(default=False)
    has_priority_support = models.BooleanField(default=False)
    max_franchises = models.PositiveIntegerField(null=True, blank=True)
    max_documents_per_franchise = models.PositiveIntegerField(null=True, blank=True)
    max_gallery_images = models.PositiveIntegerField(default=0)
    max_description_length = models.PositiveIntegerField(default=1200)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "price_monthly", "name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class FranchiseSubscription(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Oczekuje na aktywację"),
        (STATUS_ACTIVE, "Aktywna"),
        (STATUS_PAST_DUE, "Płatność zaległa"),
        (STATUS_CANCELLED, "Anulowana"),
        (STATUS_EXPIRED, "Wygasła"),
    )

    PAYMENT_PENDING = "pending"
    PAYMENT_PAID = "paid"
    PAYMENT_OVERDUE = "overdue"
    PAYMENT_NOT_REQUIRED = "not_required"
    PAYMENT_STATUS_CHOICES = (
        (PAYMENT_PENDING, "Oczekuje"),
        (PAYMENT_PAID, "Opłacona"),
        (PAYMENT_OVERDUE, "Zaległa"),
        (PAYMENT_NOT_REQUIRED, "Nie jest wymagana"),
    )

    INTERVAL_MONTHLY = "monthly"
    INTERVAL_YEARLY = "yearly"
    BILLING_INTERVAL_CHOICES = (
        (INTERVAL_MONTHLY, "Miesięcznie"),
        (INTERVAL_YEARLY, "Rocznie"),
    )

    franchise = models.OneToOneField(
        "franchises.Franchise",
        on_delete=models.CASCADE,
        related_name="billing_subscription",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="franchise_subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    billing_interval = models.CharField(
        max_length=20,
        choices=BILLING_INTERVAL_CHOICES,
        blank=True,
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, db_index=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    stripe_status = models.CharField(max_length=100, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    manual_payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_franchise_subscriptions",
    )
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ends_at", "franchise__name"]
        indexes = [
            models.Index(fields=["status", "ends_at"]),
            models.Index(fields=["plan", "status"]),
            models.Index(fields=["manual_payment_status"]),
        ]

    def __str__(self):
        return f"{self.franchise} - {self.plan} ({self.status})"


class BillingCustomer(models.Model):
    organization = models.OneToOneField(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="billing_customer",
    )
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["stripe_customer_id"])]

    def __str__(self):
        return f"{self.organization} ({self.stripe_customer_id})"


class StripeWebhookEvent(models.Model):
    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=255)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["stripe_event_id"]),
            models.Index(fields=["event_type"]),
            models.Index(fields=["processed"]),
        ]

    def __str__(self):
        return f"{self.event_type}: {self.stripe_event_id}"


class FranchiseSubscriptionRequest(models.Model):
    TYPE_START = "start"
    TYPE_EXTEND = "extend"
    TYPE_CHANGE_PLAN = "change_plan"
    TYPE_CANCEL = "cancel"
    REQUEST_TYPE_CHOICES = (
        (TYPE_START, "Zakup subskrypcji"),
        (TYPE_EXTEND, "Przedłużenie"),
        (TYPE_CHANGE_PLAN, "Zmiana planu"),
        (TYPE_CANCEL, "Anulowanie"),
    )

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Oczekuje"),
        (STATUS_APPROVED, "Zatwierdzone"),
        (STATUS_REJECTED, "Odrzucone"),
        (STATUS_CANCELLED, "Wycofane"),
    )

    DURATION_CHOICES = (
        (1, "1 miesiąc"),
        (3, "3 miesiące"),
        (12, "12 miesięcy"),
    )

    franchise = models.ForeignKey(
        "franchises.Franchise",
        on_delete=models.CASCADE,
        related_name="subscription_requests",
    )
    subscription = models.ForeignKey(
        FranchiseSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="change_requests",
    )
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPE_CHOICES)
    requested_plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="franchise_subscription_requests",
    )
    duration_months = models.PositiveSmallIntegerField(choices=DURATION_CHOICES, default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="franchise_subscription_change_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_franchise_subscription_requests",
    )
    vendor_notes = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["franchise"],
                condition=models.Q(status="pending"),
                name="unique_pending_subscription_request_per_franchise",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["franchise", "status"]),
            models.Index(fields=["request_type", "status"]),
        ]

    def __str__(self):
        return f"{self.franchise} - {self.get_request_type_display()} ({self.status})"


class OrganizationSubscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_TRIAL, "Trial"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAST_DUE, "Past due"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
    )

    PAYMENT_NOT_REQUIRED = "not_required"
    PAYMENT_PENDING = "pending"
    PAYMENT_PAID = "paid"
    PAYMENT_OVERDUE = "overdue"
    PAYMENT_STATUS_CHOICES = (
        (PAYMENT_NOT_REQUIRED, "Not required"),
        (PAYMENT_PENDING, "Pending"),
        (PAYMENT_PAID, "Paid"),
        (PAYMENT_OVERDUE, "Overdue"),
    )

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    manual_payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_NOT_REQUIRED,
    )
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at", "organization__name"]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["starts_at", "ends_at"]),
            models.Index(fields=["manual_payment_status"]),
        ]

    def __str__(self):
        return f"{self.organization} - {self.plan} ({self.status})"


class FranchisePromotion(models.Model):
    TYPE_FEATURED = "featured"
    TYPE_SEARCH_BOOST = "search_boost"
    TYPE_VERIFIED_BADGE = "verified_badge"
    TYPE_CATEGORY_TOP = "category_top"
    TYPE_HOMEPAGE_FEATURED = "homepage_featured"
    PROMOTION_TYPE_CHOICES = (
        (TYPE_FEATURED, "Featured"),
        (TYPE_SEARCH_BOOST, "Search boost"),
        (TYPE_VERIFIED_BADGE, "Verified badge"),
        (TYPE_CATEGORY_TOP, "Category top"),
        (TYPE_HOMEPAGE_FEATURED, "Homepage featured"),
    )

    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_EXPIRED, "Expired"),
    )

    franchise = models.ForeignKey(
        "franchises.Franchise",
        on_delete=models.CASCADE,
        related_name="promotions",
    )
    promotion_type = models.CharField(max_length=40, choices=PROMOTION_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveIntegerField(default=0)
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "-starts_at"]
        indexes = [
            models.Index(fields=["franchise", "status"]),
            models.Index(fields=["promotion_type", "status"]),
            models.Index(fields=["starts_at", "ends_at"]),
            models.Index(fields=["priority"]),
        ]

    def __str__(self):
        return f"{self.franchise} - {self.promotion_type} ({self.status})"


class InvestorServiceRequest(models.Model):
    SERVICE_LOCATION_REPORT = "location_report"
    SERVICE_SPECIALIST_MATCH = "specialist_match"
    SERVICE_CHOICES = (
        (SERVICE_LOCATION_REPORT, "Raport lokalizacji"),
        (SERVICE_SPECIALIST_MATCH, "Dopasowanie specjalisty"),
    )

    STATUS_NEW = "new"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_NEW, "Nowe"),
        (STATUS_IN_PROGRESS, "W realizacji"),
        (STATUS_COMPLETED, "Zakończone"),
        (STATUS_CANCELLED, "Anulowane"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investor_service_requests",
    )
    service_type = models.CharField(max_length=30, choices=SERVICE_CHOICES)
    specialist_area = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    city = models.CharField(max_length=120, blank=True)
    message = models.TextField(blank=True)
    privacy_consent = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["service_type", "status"]),
            models.Index(fields=["email"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.get_service_type_display()} - {self.name}"
