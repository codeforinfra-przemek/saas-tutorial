from django.conf import settings
from django.db import models
from django.urls import reverse


class PublishableQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Article.STATUS_PUBLISHED)


class ArticleCategory(models.Model):
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Article(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ARCHIVED, "Archived"),
    )

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    category = models.ForeignKey(
        ArticleCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
    )
    excerpt = models.TextField(blank=True)
    body = models.TextField()
    featured_image = models.FileField(upload_to="content/articles/", blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_articles",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_featured = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    seo_title = models.CharField(max_length=220, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    canonical_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PublishableQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "published_at"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["is_featured"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:article_detail", kwargs={"slug": self.slug})

    @property
    def meta_title(self):
        return self.seo_title or self.title

    @property
    def meta_description(self):
        return self.seo_description or self.excerpt


class LandingPage(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = Article.STATUS_CHOICES
    BUSINESS_TYPE_CHOICES = (
        ("", "Any"),
        ("stationary", "Stationary"),
        ("mobile", "Mobile"),
        ("online", "Online"),
        ("hybrid", "Hybrid"),
    )

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    subtitle = models.CharField(max_length=260, blank=True)
    intro = models.TextField(blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_featured = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    seo_title = models.CharField(max_length=220, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    canonical_url = models.URLField(blank=True)
    cta_label = models.CharField(max_length=120, blank=True)
    cta_url = models.CharField(max_length=300, blank=True)
    related_category = models.ForeignKey(
        "franchises.FranchiseCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landing_pages",
    )
    max_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    business_type = models.CharField(max_length=20, choices=BUSINESS_TYPE_CHOICES, blank=True)
    home_based = models.BooleanField(null=True, blank=True)
    part_time_possible = models.BooleanField(null=True, blank=True)
    training_provided = models.BooleanField(null=True, blank=True)
    financing_available = models.BooleanField(null=True, blank=True)
    selected_franchises = models.ManyToManyField("franchises.Franchise", blank=True, related_name="landing_pages")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "published_at"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["is_featured"]),
            models.Index(fields=["max_investment"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:landing_page_detail", kwargs={"slug": self.slug})

    @property
    def meta_title(self):
        return self.seo_title or self.title

    @property
    def meta_description(self):
        return self.seo_description or self.intro or self.subtitle
