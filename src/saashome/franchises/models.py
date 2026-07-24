import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateResearchStorage(FileSystemStorage):
    """A migration-safe storage rooted outside publicly served MEDIA_ROOT."""

    def __init__(self):
        super().__init__(
            location=getattr(
                settings,
                "PRIVATE_RESEARCH_UPLOAD_ROOT",
                settings.BASE_DIR / "private_research_uploads",
            ),
            base_url=None,
        )


private_research_storage = PrivateResearchStorage()


def research_document_upload_to(instance, filename):
    """Keep private uploads outside public media and discard unsafe filenames."""

    suffix = Path(filename).suffix.lower()[:12]
    return f"{instance.workspace.workspace_id}/{uuid.uuid4().hex}{suffix}"


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

    def get_absolute_url(self):
        return reverse("seo:category_detail", kwargs={"slug": self.slug})


class Franchise(models.Model):
    MARKET_STATUS_LISTED = "listed"
    MARKET_STATUS_ACTIVE = "active"
    MARKET_STATUS_INACTIVE = "inactive"
    MARKET_STATUS_UNCERTAIN = "uncertain"
    MARKET_STATUS_CHOICES = (
        (MARKET_STATUS_LISTED, "Wpis w aktualnym katalogu — do walidacji"),
        (MARKET_STATUS_ACTIVE, "Aktywna — potwierdzone"),
        (MARKET_STATUS_INACTIVE, "Nieaktywna / zamknięta"),
        (MARKET_STATUS_UNCERTAIN, "Status niepewny"),
    )
    RECRUITMENT_LISTED = "listed_offer"
    RECRUITMENT_OPEN = "confirmed_open"
    RECRUITMENT_CLOSED = "not_recruiting"
    RECRUITMENT_UNKNOWN = "unknown"
    RECRUITMENT_STATUS_CHOICES = (
        (RECRUITMENT_LISTED, "Oferta widoczna w katalogu — do walidacji"),
        (RECRUITMENT_OPEN, "Nabór potwierdzony"),
        (RECRUITMENT_CLOSED, "Brak naboru"),
        (RECRUITMENT_UNKNOWN, "Nieustalony"),
    )
    WEBSITE_MISSING = "missing"
    WEBSITE_UNVERIFIED = "unverified_seed"
    WEBSITE_VALIDATED = "validated_official"
    WEBSITE_REJECTED = "rejected"
    WEBSITE_STATUS_CHOICES = (
        (WEBSITE_MISSING, "Brak"),
        (WEBSITE_UNVERIFIED, "Niezweryfikowany seed"),
        (WEBSITE_VALIDATED, "Zweryfikowana strona oficjalna"),
        (WEBSITE_REJECTED, "Odrzucona"),
    )
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
    DATA_STATUS_RESEARCH_REVIEWED = "research_reviewed"
    DATA_STATUS_RESEARCH_WITH_GAPS = "research_with_gaps"
    DATA_STATUS_CHOICES = [
        (DATA_STATUS_DEMO, "Dane demonstracyjne"),
        (DATA_STATUS_VENDOR, "Dane przekazane przez franczyzodawcę"),
        (DATA_STATUS_EDITOR_VERIFIED, "Dane zweryfikowane przez redakcję"),
        (DATA_STATUS_RESEARCH_REVIEWED, "Research zatwierdzony przez redakcję"),
        (DATA_STATUS_RESEARCH_WITH_GAPS, "Research zatwierdzony z brakami"),
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
    website_url_status = models.CharField(
        max_length=24,
        choices=WEBSITE_STATUS_CHOICES,
        default=WEBSITE_MISSING,
    )
    market_status = models.CharField(
        max_length=20,
        choices=MARKET_STATUS_CHOICES,
        default=MARKET_STATUS_UNCERTAIN,
    )
    recruitment_status = models.CharField(
        max_length=24,
        choices=RECRUITMENT_STATUS_CHOICES,
        default=RECRUITMENT_UNKNOWN,
    )
    market_status_checked_at = models.DateField(null=True, blank=True)
    catalog_sources = models.JSONField(default=list, blank=True)
    catalog_imported_at = models.DateTimeField(null=True, blank=True)
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
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(data_status="demo")
                | models.Q(is_verified=False),
                name="demo_franchise_cannot_be_verified",
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("franchises:detail", kwargs={"slug": self.slug})

    @property
    def has_public_financial_data(self):
        """Whether numeric commercial fields may be shown in public UI.

        Demo values remain useful for local layout development, but must never
        participate in public cards, filters, comparisons or rankings.
        """

        return self.data_status != self.DATA_STATUS_DEMO


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


class FranchiseResearchImport(models.Model):
    DECISION_APPROVED = "approved"
    DECISION_APPROVED_WITH_GAPS = "approved_with_gaps"
    DECISION_CHOICES = (
        (DECISION_APPROVED, "Approved"),
        (DECISION_APPROVED_WITH_GAPS, "Approved with gaps"),
    )

    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="research_imports",
    )
    review_id = models.UUIDField(unique=True)
    normalization_id = models.UUIDField(unique=True)
    plan_run_id = models.UUIDField()
    search_id = models.UUIDField()
    extraction_id = models.UUIDField()
    check_id = models.UUIDField()
    target_country = models.CharField(max_length=2)
    depth = models.CharField(max_length=30)
    profile_id = models.CharField(max_length=80, blank=True)
    decision = models.CharField(max_length=30, choices=DECISION_CHOICES)
    reviewer = models.CharField(max_length=300)
    reviewer_notes = models.TextField(blank=True)
    incomplete_input_acknowledged = models.BooleanField(default=False)
    checker_passed = models.BooleanField()
    scope_complete = models.BooleanField()
    quality_score = models.PositiveSmallIntegerField()
    quality_threshold = models.PositiveSmallIntegerField()
    planned_tasks = models.PositiveIntegerField()
    evaluated_tasks = models.PositiveIntegerField()
    planned_fields = models.PositiveIntegerField()
    evaluated_fields = models.PositiveIntegerField()
    normalized_values_count = models.PositiveIntegerField()
    source_count = models.PositiveIntegerField()
    claim_count = models.PositiveIntegerField()
    citation_count = models.PositiveIntegerField()
    review_reference = models.TextField()
    review_sha256 = models.CharField(max_length=64)
    normalized_reference = models.TextField()
    normalized_sha256 = models.CharField(max_length=64)
    is_current = models.BooleanField(default=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]
        indexes = [
            models.Index(fields=["franchise", "is_current"]),
            models.Index(fields=["plan_run_id"]),
            models.Index(fields=["decision", "imported_at"]),
        ]

    def __str__(self):
        return f"{self.franchise} research {self.normalization_id}"

    @property
    def field_coverage_percent(self):
        if not self.planned_fields:
            return 0
        return round(self.evaluated_fields * 100 / self.planned_fields)


class FranchiseResearchArtifact(models.Model):
    TYPE_PLAN = "plan"
    TYPE_SEARCH = "search"
    TYPE_EXTRACTION = "extraction"
    TYPE_CHECK = "check"
    TYPE_NORMALIZATION = "normalization"
    TYPE_REVIEW = "review"
    TYPE_FINALIZATION = "finalization"
    TYPE_CHOICES = (
        (TYPE_PLAN, "Plan"),
        (TYPE_SEARCH, "Search"),
        (TYPE_EXTRACTION, "Extraction"),
        (TYPE_CHECK, "Check"),
        (TYPE_NORMALIZATION, "Normalization"),
        (TYPE_REVIEW, "Review"),
        (TYPE_FINALIZATION, "Workbench finalization"),
    )

    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    artifact_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    external_id = models.CharField(max_length=100)
    schema_version = models.CharField(max_length=30, blank=True)
    prompt_version = models.CharField(max_length=60, blank=True)
    reference = models.TextField()
    sha256 = models.CharField(max_length=64)
    payload = models.JSONField()

    class Meta:
        ordering = ["artifact_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "artifact_type"],
                name="unique_research_artifact_type_per_import",
            )
        ]


class FranchiseResearchTask(models.Model):
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    task_id = models.CharField(max_length=200)
    catalog_question_id = models.CharField(max_length=200)
    section_id = models.CharField(max_length=120)
    title = models.CharField(max_length=500)
    question = models.TextField()
    requirement = models.CharField(max_length=30)
    priority = models.CharField(max_length=30)
    status = models.CharField(max_length=40)
    is_evaluated = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ["sort_order", "task_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "task_id"],
                name="unique_research_task_per_import",
            )
        ]


class FranchiseResearchField(models.Model):
    task = models.ForeignKey(
        FranchiseResearchTask,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    target_field = models.CharField(max_length=500)
    requirement = models.CharField(max_length=30)
    priority = models.CharField(max_length=30)
    status = models.CharField(max_length=40)
    checker_status = models.CharField(max_length=40)
    is_evaluated = models.BooleanField(default=False)
    is_critical = models.BooleanField(default=False)
    normalized_field_id = models.CharField(max_length=100, blank=True)
    accepted_claim_ids = models.JSONField(default=list)
    needs_review_claim_ids = models.JSONField(default=list)
    rejected_claim_ids = models.JSONField(default=list)
    notes = models.JSONField(default=list)

    class Meta:
        ordering = ["task__sort_order", "target_field"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "target_field"],
                name="unique_research_field_per_task",
            )
        ]
        indexes = [models.Index(fields=["target_field", "status"])]


class FranchiseResearchSource(models.Model):
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    source_id = models.CharField(max_length=100)
    canonical_url = models.URLField(max_length=4000)
    title = models.CharField(max_length=500, blank=True)
    source_type = models.CharField(max_length=60)
    origin = models.CharField(max_length=60)
    provider_observed = models.BooleanField(default=False)
    retrieval_status = models.CharField(max_length=60, blank=True)
    task_ids = models.JSONField(default=list)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ["source_type", "title", "source_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "source_id"],
                name="unique_research_source_per_import",
            )
        ]


class FranchiseResearchClaim(models.Model):
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="claims",
    )
    field = models.ForeignKey(
        FranchiseResearchField,
        on_delete=models.SET_NULL,
        related_name="claims",
        null=True,
        blank=True,
    )
    claim_id = models.CharField(max_length=100)
    task_id = models.CharField(max_length=200)
    target_field = models.CharField(max_length=500)
    value_text = models.TextField()
    asserted_by_text = models.TextField(blank=True)
    as_of_text = models.CharField(max_length=300, blank=True)
    unit_text = models.CharField(max_length=300, blank=True)
    currency_text = models.CharField(max_length=100, blank=True)
    publication_date_text = models.CharField(max_length=300, blank=True)
    effective_date_text = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)
    checker_verdict = models.CharField(max_length=40, blank=True)
    semantic_fit = models.CharField(max_length=40, blank=True)
    source_support = models.CharField(max_length=40, blank=True)
    issue_codes = models.JSONField(default=list)
    is_eligible = models.BooleanField(default=False)
    is_excluded = models.BooleanField(default=False)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ["task_id", "target_field", "claim_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "claim_id"],
                name="unique_research_claim_per_import",
            )
        ]
        indexes = [models.Index(fields=["target_field", "checker_verdict"])]


class FranchiseResearchCitation(models.Model):
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="citations",
    )
    source = models.ForeignKey(
        FranchiseResearchSource,
        on_delete=models.SET_NULL,
        related_name="citations",
        null=True,
        blank=True,
    )
    citation_id = models.CharField(max_length=100)
    passage_id = models.CharField(max_length=100)
    document_id = models.CharField(max_length=100)
    quote = models.TextField()
    locator = models.CharField(max_length=500, blank=True)
    text_sha256 = models.CharField(max_length=64)
    start_char = models.PositiveIntegerField(null=True, blank=True)
    end_char = models.PositiveIntegerField(null=True, blank=True)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ["source_id", "citation_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "citation_id"],
                name="unique_research_citation_per_import",
            )
        ]


class FranchiseResearchClaimCitation(models.Model):
    claim = models.ForeignKey(
        FranchiseResearchClaim,
        on_delete=models.CASCADE,
        related_name="claim_citations",
    )
    citation = models.ForeignKey(
        FranchiseResearchCitation,
        on_delete=models.CASCADE,
        related_name="citation_claims",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "citation"],
                name="unique_research_claim_citation",
            )
        ]


class FranchiseResearchValue(models.Model):
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.CASCADE,
        related_name="values",
    )
    field = models.ForeignKey(
        FranchiseResearchField,
        on_delete=models.CASCADE,
        related_name="values",
    )
    normalized_value_id = models.CharField(max_length=100)
    value_type = models.CharField(max_length=30)
    canonical_text = models.TextField()
    number_min_text = models.CharField(max_length=200, blank=True)
    number_max_text = models.CharField(max_length=200, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    date_value = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True)
    unit = models.CharField(max_length=100, blank=True)
    precision = models.CharField(max_length=30)
    notes = models.TextField(blank=True)
    raw_value_texts = models.JSONField(default=list)
    citation_ids = models.JSONField(default=list)
    source_ids = models.JSONField(default=list)
    needs_corroboration = models.BooleanField(default=False)

    class Meta:
        ordering = ["field__task__sort_order", "field__target_field", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_import", "normalized_value_id"],
                name="unique_research_value_per_import",
            )
        ]

    @property
    def display_value(self):
        if self.value_type in {"integer", "decimal", "money", "percentage"}:
            rendered = self.number_min_text
            if self.number_max_text and self.number_max_text != self.number_min_text:
                rendered = f"{rendered} – {self.number_max_text}"
            suffix = self.currency or self.unit
            if self.value_type == "percentage" and not suffix:
                suffix = "%"
            return f"{rendered} {suffix}".strip()
        if self.value_type == "boolean":
            return "Tak" if self.boolean_value else "Nie"
        if self.value_type == "date" and self.date_value:
            return self.date_value.isoformat()
        return self.canonical_text


class FranchiseResearchValueClaim(models.Model):
    value = models.ForeignKey(
        FranchiseResearchValue,
        on_delete=models.CASCADE,
        related_name="value_claims",
    )
    claim = models.ForeignKey(
        FranchiseResearchClaim,
        on_delete=models.CASCADE,
        related_name="claim_values",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["value", "claim"],
                name="unique_research_value_claim",
            )
        ]


class FranchiseResearchWorkspace(models.Model):
    """Mutable editorial staging area in front of immutable research imports."""

    STATUS_REVIEW = "review"
    STATUS_READY = "ready"
    STATUS_APPROVED = "approved"
    STATUS_APPROVED_WITH_GAPS = "approved_with_gaps"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_REVIEW, "W trakcie weryfikacji"),
        (STATUS_READY, "Gotowe do zatwierdzenia"),
        (STATUS_APPROVED, "Zatwierdzone"),
        (STATUS_APPROVED_WITH_GAPS, "Zatwierdzone z udokumentowanymi brakami"),
        (STATUS_REJECTED, "Odrzucone"),
    )

    workspace_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="research_workspaces",
    )
    normalization_id = models.UUIDField(unique=True)
    plan_run_id = models.UUIDField()
    target_country = models.CharField(max_length=2)
    depth = models.CharField(max_length=30)
    profile_id = models.CharField(max_length=80, blank=True)
    iteration = models.PositiveIntegerField()
    normalized_reference = models.TextField()
    normalized_sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_REVIEW,
    )
    quality_score = models.PositiveSmallIntegerField(default=0)
    quality_threshold = models.PositiveSmallIntegerField(default=80)
    checker_passed = models.BooleanField(default=False)
    scope_complete = models.BooleanField(default=False)
    planned_tasks = models.PositiveIntegerField(default=0)
    evaluated_tasks = models.PositiveIntegerField(default=0)
    planned_fields = models.PositiveIntegerField(default=0)
    source_count = models.PositiveIntegerField(default=0)
    claim_count = models.PositiveIntegerField(default=0)
    normalized_values_count = models.PositiveIntegerField(default=0)
    stage_summary = models.JSONField(default=list)
    cost_summary = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    reviewer_notes = models.TextField(blank=True)
    auto_reviewed = models.BooleanField(default=False)
    review_policy_version = models.CharField(max_length=80, blank=True)
    auto_review_summary = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_research_workspaces",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_research_workspaces",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["franchise", "status"]),
            models.Index(fields=["status", "updated_at"]),
        ]

    def __str__(self):
        return f"{self.franchise} workbench {self.normalization_id}"

    @property
    def review_progress_percent(self):
        total = self.review_fields.count()
        if not total:
            return 0
        reviewed = self.review_fields.exclude(
            decision=FranchiseResearchReviewField.DECISION_PENDING
        ).count()
        return round(reviewed * 100 / total)

    @property
    def is_finalized(self):
        return hasattr(self, "finalization")


class FranchiseResearchReviewField(models.Model):
    DECISION_PENDING = "pending"
    DECISION_ACCEPTED = "accepted"
    DECISION_ACCEPTED_EDITED = "accepted_edited"
    DECISION_POLICY_ACCEPTED = "policy_accepted"
    DECISION_REJECTED = "rejected"
    DECISION_DOCUMENTED_GAP = "documented_gap"
    DECISION_CHOICES = (
        (DECISION_PENDING, "Do sprawdzenia"),
        (DECISION_ACCEPTED, "Zaakceptowane"),
        (DECISION_ACCEPTED_EDITED, "Poprawione i zaakceptowane"),
        (DECISION_POLICY_ACCEPTED, "Zaakceptowane przez regułę L1"),
        (DECISION_REJECTED, "Odrzucone"),
        (DECISION_DOCUMENTED_GAP, "Sprawdzono — brak danych"),
    )

    workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.CASCADE,
        related_name="review_fields",
    )
    normalized_field_id = models.CharField(max_length=100, blank=True)
    task_id = models.CharField(max_length=200)
    task_title = models.CharField(max_length=500)
    target_field = models.CharField(max_length=500)
    requirement = models.CharField(max_length=30, blank=True)
    priority = models.CharField(max_length=30, blank=True)
    pipeline_status = models.CharField(max_length=40)
    checker_status = models.CharField(max_length=40, blank=True)
    proposed_values = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    source_ids = models.JSONField(default=list)
    notes = models.JSONField(default=list)
    decision = models.CharField(
        max_length=30,
        choices=DECISION_CHOICES,
        default=DECISION_PENDING,
    )
    reviewer_value = models.TextField(blank=True)
    reviewer_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_field_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    supporting_documents = models.ManyToManyField(
        "FranchiseResearchDocument",
        blank=True,
        related_name="supported_review_fields",
    )
    inherited_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carried_forward_to",
    )
    sort_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "target_field"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "task_id", "target_field"],
                name="unique_workbench_field_per_task",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "decision"]),
            models.Index(fields=["workspace", "pipeline_status"]),
        ]

    def __str__(self):
        return f"{self.workspace.franchise}: {self.target_field}"

    @property
    def proposed_display(self):
        return " | ".join(
            str(item.get("display") or item.get("canonical_text") or "")
            for item in self.proposed_values
        ).strip(" |")

    @property
    def effective_value(self):
        return self.reviewer_value.strip() or self.proposed_display


class FranchiseResearchDocument(models.Model):
    TYPE_CONTRACT = "contract"
    TYPE_DISCLOSURE = "disclosure"
    TYPE_FINANCIAL = "financial"
    TYPE_LEGAL = "legal"
    TYPE_PRESENTATION = "presentation"
    TYPE_OTHER = "other"
    TYPE_CHOICES = (
        (TYPE_CONTRACT, "Umowa / wzór umowy"),
        (TYPE_DISCLOSURE, "Pakiet informacyjny / oferta"),
        (TYPE_FINANCIAL, "Dane finansowe"),
        (TYPE_LEGAL, "Dokument prawny / rejestrowy"),
        (TYPE_PRESENTATION, "Prezentacja / materiały sieci"),
        (TYPE_OTHER, "Inny dokument"),
    )
    ACCESS_INTERNAL = "internal"
    ACCESS_RESTRICTED = "restricted"
    ACCESS_PUBLIC_SOURCE = "public_source"
    ACCESS_CHOICES = (
        (ACCESS_INTERNAL, "Wewnętrzny"),
        (ACCESS_RESTRICTED, "Poufny — ograniczony dostęp"),
        (ACCESS_PUBLIC_SOURCE, "Materiał z publicznego źródła"),
    )
    STATUS_PENDING = "pending"
    STATUS_READY = "ready"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Czeka na analizę"),
        (STATUS_READY, "Uwzględniony ręcznie"),
    )

    workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    file = models.FileField(
        upload_to=research_document_upload_to,
        storage=private_research_storage,
        max_length=500,
    )
    original_name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    access_level = models.CharField(
        max_length=30,
        choices=ACCESS_CHOICES,
        default=ACCESS_INTERNAL,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "sha256"],
                name="unique_workbench_document_content",
            )
        ]


class FranchiseResearchEvent(models.Model):
    workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=50)
    message = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["workspace", "created_at"])]


class FranchiseResearchJob(models.Model):
    KIND_LOOP = "loop"
    KIND_CHECK = "check"
    KIND_NORMALIZE = "normalize"
    KIND_FINALIZE = "finalize"
    KIND_CHOICES = (
        (KIND_LOOP, "Kontynuuj research"),
        (KIND_CHECK, "Ponów kontrolę jakości"),
        (KIND_NORMALIZE, "Utwórz nowy draft danych"),
        (KIND_FINALIZE, "Zamroź i opublikuj wersję"),
    )
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "W kolejce"),
        (STATUS_RUNNING, "W trakcie"),
        (STATUS_SUCCEEDED, "Zakończone"),
        (STATUS_FAILED, "Błąd"),
        (STATUS_CANCELLED, "Anulowane"),
    )

    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    input_reference = models.TextField()
    input_sha256 = models.CharField(max_length=64)
    configuration = models.JSONField(default=dict)
    current_stage = models.CharField(max_length=120, default="Oczekiwanie na worker")
    progress_percent = models.PositiveSmallIntegerField(default=0)
    log = models.TextField(blank=True)
    result_summary = models.JSONField(default=dict)
    cost_summary = models.JSONField(default=dict)
    result_loop_reference = models.TextField(blank=True)
    result_loop_sha256 = models.CharField(max_length=64, blank=True)
    result_check_reference = models.TextField(blank=True)
    result_check_sha256 = models.CharField(max_length=64, blank=True)
    result_normalized_reference = models.TextField(blank=True)
    result_normalized_sha256 = models.CharField(max_length=64, blank=True)
    result_workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_jobs",
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_research_jobs",
    )
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(status__in=["queued", "running"]),
                name="unique_active_research_job_per_workspace",
            )
        ]
        indexes = [
            models.Index(fields=["status", "queued_at"]),
            models.Index(fields=["workspace", "queued_at"]),
        ]

    def __str__(self):
        return f"{self.workspace.franchise} {self.kind} {self.status}"

    @property
    def is_active(self):
        return self.status in {self.STATUS_QUEUED, self.STATUS_RUNNING}


class FranchiseResearchCampaign(models.Model):
    """A budgeted group of durable first-run research launches."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "W kolejce"),
        (STATUS_RUNNING, "W trakcie"),
        (STATUS_COMPLETED, "Drafty gotowe do Human Review"),
        (STATUS_COMPLETED_WITH_ERRORS, "Drafty częściowo gotowe — są błędy"),
        (STATUS_CANCELLED, "Anulowana"),
    )

    campaign_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    target_country = models.CharField(max_length=2, default="PL")
    profile_id = models.CharField(max_length=80)
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    configuration = models.JSONField(default=dict)
    max_total_cost_usd = models.DecimalField(max_digits=10, decimal_places=2)
    reserved_cost_usd = models.DecimalField(max_digits=10, decimal_places=2)
    max_concurrent_runs = models.PositiveSmallIntegerField(default=1)
    cancel_requested = models.BooleanField(default=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_research_campaigns",
    )
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at", "-id"]
        indexes = [models.Index(fields=["status", "queued_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_concurrent_runs__gte=1)
                & models.Q(max_concurrent_runs__lte=5),
                name="research_campaign_concurrency_between_1_and_5",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_cost_usd__gte=0)
                & models.Q(max_total_cost_usd__gte=models.F("reserved_cost_usd")),
                name="research_campaign_budget_covers_reservation",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.profile_id})"

    @property
    def is_active(self):
        return self.status in {self.STATUS_QUEUED, self.STATUS_RUNNING}


class FranchiseResearchLaunch(models.Model):
    """Durable first-run orchestration before a Workbench exists."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_COMPLETE = "complete"
    STATUS_PARTIAL = "partial"
    STATUS_INSUFFICIENT = "insufficient"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "W kolejce"),
        (STATUS_RUNNING, "W trakcie"),
        (STATUS_SUCCEEDED, "Draft do Human Review (status historyczny)"),
        (STATUS_COMPLETE, "Pełny L1 — Draft do Human Review"),
        (STATUS_PARTIAL, "Częściowy — Draft do Human Review"),
        (STATUS_INSUFFICIENT, "Niewystarczający — wymaga uzupełnienia"),
        (STATUS_FAILED, "Błąd"),
        (STATUS_CANCELLED, "Anulowane"),
    )

    launch_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    campaign = models.ForeignKey(
        FranchiseResearchCampaign,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="launches",
    )
    campaign_position = models.PositiveIntegerField(null=True, blank=True)
    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="research_launches",
    )
    target_country = models.CharField(max_length=2, default="PL")
    profile_id = models.CharField(max_length=80)
    known_legal_name = models.CharField(max_length=300, blank=True)
    known_official_website = models.URLField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    configuration = models.JSONField(default=dict)
    current_stage = models.CharField(max_length=120, default="Oczekiwanie na worker")
    progress_percent = models.PositiveSmallIntegerField(default=0)
    log = models.TextField(blank=True)
    result_summary = models.JSONField(default=dict)
    cost_summary = models.JSONField(default=dict)
    provider_failure_history = models.JSONField(default=list)
    plan_reference = models.TextField(blank=True)
    plan_sha256 = models.CharField(max_length=64, blank=True)
    sources_reference = models.TextField(blank=True)
    sources_sha256 = models.CharField(max_length=64, blank=True)
    extractions_reference = models.TextField(blank=True)
    extractions_sha256 = models.CharField(max_length=64, blank=True)
    check_reference = models.TextField(blank=True)
    check_sha256 = models.CharField(max_length=64, blank=True)
    normalized_reference = models.TextField(blank=True)
    normalized_sha256 = models.CharField(max_length=64, blank=True)
    seed_sources_reference = models.TextField(blank=True)
    seed_extractions_reference = models.TextField(blank=True)
    seed_check_reference = models.TextField(blank=True)
    resolution_reference = models.TextField(blank=True)
    execution_reference = models.TextField(blank=True)
    result_workspace = models.ForeignKey(
        FranchiseResearchWorkspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initial_launches",
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_research_launches",
    )
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at", "-id"]
        indexes = [models.Index(fields=["status", "queued_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["franchise"],
                condition=models.Q(status__in=["queued", "running"]),
                name="unique_active_research_launch_per_franchise",
            ),
            models.UniqueConstraint(
                fields=["campaign", "franchise"],
                condition=models.Q(campaign__isnull=False),
                name="unique_franchise_per_research_campaign",
            ),
        ]

    def __str__(self):
        return f"{self.franchise} {self.profile_id} {self.status}"

    @property
    def is_active(self):
        return self.status in {self.STATUS_QUEUED, self.STATUS_RUNNING}


class FranchiseResearchFinalization(models.Model):
    """Immutable editorial release attached to one Workbench and one import."""

    finalization_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    workspace = models.OneToOneField(
        FranchiseResearchWorkspace,
        on_delete=models.PROTECT,
        related_name="finalization",
    )
    research_import = models.ForeignKey(
        FranchiseResearchImport,
        on_delete=models.PROTECT,
        related_name="workbench_finalizations",
    )
    release_number = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )
    decision = models.CharField(max_length=30, choices=FranchiseResearchImport.DECISION_CHOICES)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_finalizations",
    )
    reviewer_name = models.CharField(max_length=300)
    reviewer_notes = models.TextField(blank=True)
    normalized_sha256 = models.CharField(max_length=64)
    workspace_state_sha256 = models.CharField(max_length=64)
    artifact_reference = models.TextField()
    artifact_sha256 = models.CharField(max_length=64)
    field_count = models.PositiveIntegerField(default=0)
    accepted_count = models.PositiveIntegerField(default=0)
    edited_count = models.PositiveIntegerField(default=0)
    policy_accepted_count = models.PositiveIntegerField(default=0)
    rejected_count = models.PositiveIntegerField(default=0)
    gap_count = models.PositiveIntegerField(default=0)
    pending_count = models.PositiveIntegerField(default=0)
    document_count = models.PositiveIntegerField(default=0)
    finalized_at = models.DateTimeField()

    class Meta:
        ordering = ["-finalized_at"]
        indexes = [
            models.Index(fields=["research_import", "finalized_at"]),
            models.Index(fields=["decision", "finalized_at"]),
        ]

    def __str__(self):
        return f"{self.workspace.franchise} finalization {self.finalization_id}"


class FranchiseResearchEditorialDocument(models.Model):
    """Frozen metadata for a private Workbench document; never stores public bytes."""

    finalization = models.ForeignKey(
        FranchiseResearchFinalization,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    workbench_document = models.ForeignKey(
        FranchiseResearchDocument,
        on_delete=models.PROTECT,
        related_name="finalized_snapshots",
    )
    original_name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30)
    access_level = models.CharField(max_length=30)
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["original_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["finalization", "workbench_document"],
                name="unique_finalized_workbench_document",
            )
        ]


class FranchiseResearchEditorialDecision(models.Model):
    """Human decision overlay; AI and manually supplied values remain distinguishable."""

    ORIGIN_AI = "ai"
    ORIGIN_HUMAN = "human"
    ORIGIN_NONE = "none"
    ORIGIN_POLICY = "policy"
    ORIGIN_CHOICES = (
        (ORIGIN_AI, "AI proposal approved by a human"),
        (ORIGIN_HUMAN, "Human supplied or corrected"),
        (ORIGIN_NONE, "No publishable value"),
        (ORIGIN_POLICY, "Accepted by a versioned publication policy"),
    )

    finalization = models.ForeignKey(
        FranchiseResearchFinalization,
        on_delete=models.CASCADE,
        related_name="field_decisions",
    )
    research_field = models.ForeignKey(
        FranchiseResearchField,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="editorial_decisions",
    )
    task_id = models.CharField(max_length=200)
    task_title = models.CharField(max_length=500)
    target_field = models.CharField(max_length=500)
    requirement = models.CharField(max_length=30, blank=True)
    priority = models.CharField(max_length=30, blank=True)
    pipeline_status = models.CharField(max_length=40)
    checker_status = models.CharField(max_length=40, blank=True)
    decision = models.CharField(
        max_length=30,
        choices=FranchiseResearchReviewField.DECISION_CHOICES,
    )
    value_origin = models.CharField(max_length=10, choices=ORIGIN_CHOICES)
    effective_value = models.TextField(blank=True)
    proposed_values = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    source_ids = models.JSONField(default=list)
    reviewer_note = models.TextField(blank=True)
    decided_by_name = models.CharField(max_length=300, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    supporting_documents = models.ManyToManyField(
        FranchiseResearchEditorialDocument,
        blank=True,
        related_name="field_decisions",
    )

    class Meta:
        ordering = ["research_field__task__sort_order", "target_field", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["finalization", "task_id", "target_field"],
                name="unique_editorial_field_per_finalization",
            )
        ]
        indexes = [
            models.Index(fields=["finalization", "decision"]),
            models.Index(fields=["target_field", "decision"]),
        ]

    @property
    def is_public(self):
        return self.decision in {
            FranchiseResearchReviewField.DECISION_ACCEPTED,
            FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        }


class FranchiseResearchPublishedField(models.Model):
    """Audited projection of one approved editorial value onto Franchise."""

    STATUS_PROJECTED = "projected"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = (
        (STATUS_PROJECTED, "Opublikowano na profilu"),
        (STATUS_SKIPPED, "Pozostawiono tylko w raporcie"),
    )

    franchise = models.ForeignKey(
        Franchise,
        on_delete=models.CASCADE,
        related_name="research_published_fields",
    )
    finalization = models.ForeignKey(
        FranchiseResearchFinalization,
        on_delete=models.PROTECT,
        related_name="published_fields",
    )
    editorial_decision = models.OneToOneField(
        FranchiseResearchEditorialDecision,
        on_delete=models.PROTECT,
        related_name="profile_projection",
    )
    target_field = models.CharField(max_length=500)
    franchise_attribute = models.CharField(max_length=80)
    value_origin = models.CharField(
        max_length=10,
        choices=FranchiseResearchEditorialDecision.ORIGIN_CHOICES,
    )
    effective_value = models.TextField()
    previous_value = models.JSONField(null=True, blank=True)
    projected_value = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    issue_code = models.CharField(max_length=80, blank=True)
    is_current = models.BooleanField(default=False)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["franchise_attribute", "target_field", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["finalization", "franchise_attribute"],
                name="unique_publication_attribute_per_finalization",
            ),
            models.UniqueConstraint(
                fields=["franchise", "franchise_attribute"],
                condition=models.Q(is_current=True, status="projected"),
                name="unique_current_research_publication_attribute",
            ),
        ]
        indexes = [
            models.Index(fields=["franchise", "is_current"]),
            models.Index(fields=["finalization", "status"]),
        ]

    def __str__(self):
        return f"{self.franchise}: {self.target_field} → {self.franchise_attribute}"


@receiver(post_delete, sender=FranchiseResearchDocument)
def delete_private_research_file(sender, instance, **kwargs):
    """Do not leave confidential uploads behind after their record is deleted."""

    if instance.file:
        instance.file.delete(save=False)
