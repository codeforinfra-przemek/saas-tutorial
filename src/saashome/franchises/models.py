from django.db import models
from django.urls import reverse


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

    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    category = models.ForeignKey(
        FranchiseCategory,
        on_delete=models.PROTECT,
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
