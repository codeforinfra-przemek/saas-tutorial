from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    USER_TYPE_USER = "user"
    USER_TYPE_VENDOR = "vendor"
    USER_TYPE_CHOICES = (
        (USER_TYPE_USER, "User"),
        (USER_TYPE_VENDOR, "Vendor"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    user_type = models.CharField(
        max_length=20,
        choices=USER_TYPE_CHOICES,
        default=USER_TYPE_USER,
    )
    email_verified = models.BooleanField(default=False)
    avatar = models.FileField(upload_to="avatars/", blank=True)
    headline = models.CharField(max_length=140, blank=True)
    bio = models.TextField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} profile"


class Organization(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_SUSPENDED = "suspended"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_SUSPENDED, "Suspended"),
    )
    PACKAGE_FREE = "free"
    PACKAGE_BASIC = "basic"
    PACKAGE_PREMIUM = "premium"
    PACKAGE_ENTERPRISE = "enterprise"
    PACKAGE_CHOICES = (
        (PACKAGE_FREE, "Free"),
        (PACKAGE_BASIC, "Basic"),
        (PACKAGE_PREMIUM, "Premium"),
        (PACKAGE_ENTERPRISE, "Enterprise"),
    )

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    website_url = models.URLField(blank=True)
    contact_email = models.EmailField(blank=True)
    billing_email = models.EmailField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES, default=PACKAGE_FREE)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = (
        (ROLE_OWNER, "Owner"),
        (ROLE_ADMIN, "Admin"),
        (ROLE_MEMBER, "Member"),
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("organization", "user")
        ordering = ["organization__name", "user__email"]

    def __str__(self):
        return f"{self.user} in {self.organization} ({self.role})"
