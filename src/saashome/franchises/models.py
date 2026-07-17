from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


FRANCHISE_VENDOR_EDITABLE_FIELDS = (
    "short_description",
    "description",
    "website_url",
    "min_investment",
    "max_investment",
    "initial_fee",
    "royalty_fee_text",
    "marketing_fee_text",
    "business_type",
    "required_premises",
    "home_based",
    "part_time_possible",
    "training_provided",
    "financing_available",
    "founded_year",
    "franchising_since",
    "total_units",
    "poland_units",
    "franchised_units",
    "company_owned_units",
    "units_opened_last_year",
    "units_closed_last_year",
    "units_transferred_last_year",
    "unit_growth_percent_1y",
    "liquid_capital_required",
    "net_worth_required",
    "franchise_term_years",
    "renewal_term_years",
    "estimated_payback_months",
    "mature_unit_revenue_annual",
    "mature_unit_operating_profit_annual",
    "mature_unit_count",
    "typical_unit_size_min_sqm",
    "typical_unit_size_max_sqm",
    "typical_staff_count",
    "territory_type",
    "financial_performance_disclosed",
    "financial_performance_note",
    "financial_data_as_of",
    "data_status",
    "data_source_url",
)


class FranchiseCategory(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "franchise category"
        verbose_name_plural = "franchise categories"

    def __str__(self):
        return self.name


class Franchise(models.Model):
    BUSINESS_TYPE_STATIONARY = "stationary"
    BUSINESS_TYPE_MOBILE = "mobile"
    BUSINESS_TYPE_ONLINE = "online"
    BUSINESS_TYPE_HYBRID = "hybrid"
    BUSINESS_TYPE_CHOICES = [
        (BUSINESS_TYPE_STATIONARY, "Stationary"),
        (BUSINESS_TYPE_MOBILE, "Mobile"),
        (BUSINESS_TYPE_ONLINE, "Online"),
        (BUSINESS_TYPE_HYBRID, "Hybrid"),
    ]
    TERRITORY_EXCLUSIVE = "exclusive"
    TERRITORY_PROTECTED = "protected"
    TERRITORY_NON_EXCLUSIVE = "non_exclusive"
    TERRITORY_NOT_DISCLOSED = "not_disclosed"
    TERRITORY_TYPE_CHOICES = [
        (TERRITORY_EXCLUSIVE, "Wyłączna"),
        (TERRITORY_PROTECTED, "Chroniona"),
        (TERRITORY_NON_EXCLUSIVE, "Niewyłączna"),
        (TERRITORY_NOT_DISCLOSED, "Do ustalenia w umowie"),
    ]
    DATA_STATUS_DEMO = "demo"
    DATA_STATUS_VENDOR = "vendor"
    DATA_STATUS_EDITOR_VERIFIED = "editor_verified"
    DATA_STATUS_CHOICES = [
        (DATA_STATUS_DEMO, "Dane demonstracyjne"),
        (DATA_STATUS_VENDOR, "Dane przekazane przez franczyzodawcę"),
        (DATA_STATUS_EDITOR_VERIFIED, "Dane zweryfikowane przez redakcję"),
    ]

    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    category = models.ForeignKey(
        FranchiseCategory,
        on_delete=models.PROTECT,
        related_name="franchises",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="franchises",
    )
    short_description = models.CharField(max_length=260)
    description = models.TextField(blank=True)
    logo = models.FileField(upload_to="franchise_logos/", blank=True)
    website_url = models.URLField(blank=True)
    min_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    initial_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    royalty_fee_text = models.CharField(max_length=160, blank=True)
    marketing_fee_text = models.CharField(max_length=160, blank=True)
    business_type = models.CharField(
        max_length=20,
        choices=BUSINESS_TYPE_CHOICES,
        default=BUSINESS_TYPE_STATIONARY,
    )
    required_premises = models.CharField(max_length=180, blank=True)
    home_based = models.BooleanField(default=False)
    part_time_possible = models.BooleanField(default=False)
    training_provided = models.BooleanField(default=True)
    financing_available = models.BooleanField(default=False)
    founded_year = models.PositiveIntegerField(null=True, blank=True)
    franchising_since = models.PositiveIntegerField(null=True, blank=True)
    total_units = models.PositiveIntegerField(null=True, blank=True)
    poland_units = models.PositiveIntegerField(null=True, blank=True)
    franchised_units = models.PositiveIntegerField(null=True, blank=True)
    company_owned_units = models.PositiveIntegerField(null=True, blank=True)
    units_opened_last_year = models.PositiveIntegerField(null=True, blank=True)
    units_closed_last_year = models.PositiveIntegerField(null=True, blank=True)
    units_transferred_last_year = models.PositiveIntegerField(null=True, blank=True)
    unit_growth_percent_1y = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    liquid_capital_required = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    net_worth_required = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    franchise_term_years = models.PositiveSmallIntegerField(null=True, blank=True)
    renewal_term_years = models.PositiveSmallIntegerField(null=True, blank=True)
    estimated_payback_months = models.PositiveSmallIntegerField(null=True, blank=True)
    mature_unit_revenue_annual = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    mature_unit_operating_profit_annual = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    mature_unit_count = models.PositiveIntegerField(null=True, blank=True)
    typical_unit_size_min_sqm = models.PositiveIntegerField(null=True, blank=True)
    typical_unit_size_max_sqm = models.PositiveIntegerField(null=True, blank=True)
    typical_staff_count = models.PositiveSmallIntegerField(null=True, blank=True)
    territory_type = models.CharField(max_length=20, choices=TERRITORY_TYPE_CHOICES, blank=True)
    financial_performance_disclosed = models.BooleanField(default=False)
    financial_performance_note = models.TextField(blank=True)
    financial_data_as_of = models.DateField(null=True, blank=True)
    data_status = models.CharField(max_length=20, choices=DATA_STATUS_CHOICES, default=DATA_STATUS_DEMO)
    data_source_url = models.URLField(blank=True)
    rank_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    popularity_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    editor_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    is_promoted = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_promoted", "-rank_score", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("franchises:detail", kwargs={"slug": self.slug})


class FranchiseLocation(models.Model):
    LOCATION_TYPE_EXISTING_UNIT = "existing_unit"
    LOCATION_TYPE_AVAILABLE_AREA = "available_area"
    LOCATION_TYPE_HEADQUARTERS = "headquarters"
    LOCATION_TYPE_CHOICES = [
        (LOCATION_TYPE_EXISTING_UNIT, "Existing unit"),
        (LOCATION_TYPE_AVAILABLE_AREA, "Available area"),
        (LOCATION_TYPE_HEADQUARTERS, "Headquarters"),
    ]

    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="locations",
    )
    location_type = models.CharField(
        max_length=30,
        choices=LOCATION_TYPE_CHOICES,
        default=LOCATION_TYPE_EXISTING_UNIT,
    )
    name = models.CharField(max_length=160)
    city = models.CharField(max_length=120)
    region = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=220, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["city", "name"]

    def __str__(self):
        return f"{self.franchise.name} - {self.city}"


class FranchiseAsset(models.Model):
    TYPE_IMAGE = "image"
    TYPE_DOCUMENT = "document"
    ASSET_TYPE_CHOICES = (
        (TYPE_IMAGE, "Zdjęcie"),
        (TYPE_DOCUMENT, "Dokument"),
    )

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Oczekuje na moderację"),
        (STATUS_APPROVED, "Zatwierdzony"),
        (STATUS_REJECTED, "Odrzucony"),
    )

    franchise = models.ForeignKey(Franchise, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPE_CHOICES)
    title = models.CharField(max_length=160)
    description = models.CharField(max_length=260, blank=True)
    file = models.FileField(upload_to="franchise_assets/%Y/%m/")
    sort_order = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_franchise_assets",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_franchise_assets",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_type", "sort_order", "created_at"]
        indexes = [
            models.Index(fields=["franchise", "asset_type", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.franchise} - {self.title}"


class FranchiseUpdateRequest(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="update_requests",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="franchise_update_requests",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="submitted_franchise_updates",
        null=True,
        blank=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_franchise_updates",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    short_description = models.CharField(max_length=260)
    description = models.TextField(blank=True)
    website_url = models.URLField(blank=True)
    min_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    initial_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    royalty_fee_text = models.CharField(max_length=160, blank=True)
    marketing_fee_text = models.CharField(max_length=160, blank=True)
    business_type = models.CharField(
        max_length=20,
        choices=Franchise.BUSINESS_TYPE_CHOICES,
        default=Franchise.BUSINESS_TYPE_STATIONARY,
    )
    required_premises = models.CharField(max_length=180, blank=True)
    home_based = models.BooleanField(default=False)
    part_time_possible = models.BooleanField(default=False)
    training_provided = models.BooleanField(default=True)
    financing_available = models.BooleanField(default=False)
    founded_year = models.PositiveIntegerField(null=True, blank=True)
    franchising_since = models.PositiveIntegerField(null=True, blank=True)
    total_units = models.PositiveIntegerField(null=True, blank=True)
    poland_units = models.PositiveIntegerField(null=True, blank=True)
    franchised_units = models.PositiveIntegerField(null=True, blank=True)
    company_owned_units = models.PositiveIntegerField(null=True, blank=True)
    units_opened_last_year = models.PositiveIntegerField(null=True, blank=True)
    units_closed_last_year = models.PositiveIntegerField(null=True, blank=True)
    units_transferred_last_year = models.PositiveIntegerField(null=True, blank=True)
    unit_growth_percent_1y = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    liquid_capital_required = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    net_worth_required = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    franchise_term_years = models.PositiveSmallIntegerField(null=True, blank=True)
    renewal_term_years = models.PositiveSmallIntegerField(null=True, blank=True)
    estimated_payback_months = models.PositiveSmallIntegerField(null=True, blank=True)
    mature_unit_revenue_annual = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    mature_unit_operating_profit_annual = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    mature_unit_count = models.PositiveIntegerField(null=True, blank=True)
    typical_unit_size_min_sqm = models.PositiveIntegerField(null=True, blank=True)
    typical_unit_size_max_sqm = models.PositiveIntegerField(null=True, blank=True)
    typical_staff_count = models.PositiveSmallIntegerField(null=True, blank=True)
    territory_type = models.CharField(max_length=20, choices=Franchise.TERRITORY_TYPE_CHOICES, blank=True)
    financial_performance_disclosed = models.BooleanField(default=False)
    financial_performance_note = models.TextField(blank=True)
    financial_data_as_of = models.DateField(null=True, blank=True)
    data_status = models.CharField(max_length=20, choices=Franchise.DATA_STATUS_CHOICES, default=Franchise.DATA_STATUS_DEMO)
    data_source_url = models.URLField(blank=True)

    admin_feedback = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["franchise", "status"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["submitted_by", "status"]),
            models.Index(fields=["status", "submitted_at"]),
        ]

    def __str__(self):
        return f"{self.franchise} update ({self.status})"

    def submit(self):
        if self.status not in (self.STATUS_DRAFT, self.STATUS_REJECTED):
            return
        self.status = self.STATUS_SUBMITTED
        self.submitted_at = timezone.now()
        self.reviewed_by = None
        self.reviewed_at = None
        self.save(update_fields=["status", "submitted_at", "reviewed_by", "reviewed_at", "updated_at"])

    def approve(self, reviewed_by=None):
        for field_name in FRANCHISE_VENDOR_EDITABLE_FIELDS:
            if hasattr(self.franchise, field_name) and hasattr(self, field_name):
                setattr(self.franchise, field_name, getattr(self, field_name))
        self.franchise.save(update_fields=[field for field in FRANCHISE_VENDOR_EDITABLE_FIELDS if hasattr(self.franchise, field)])
        self.status = self.STATUS_APPROVED
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    def reject(self, reviewed_by=None, feedback=""):
        self.status = self.STATUS_REJECTED
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        if feedback:
            self.admin_feedback = feedback
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_feedback", "updated_at"])
