from decimal import Decimal

from django.conf import settings
from django.db import models


class RevenueEvent(models.Model):
    EVENT_NEW_SUBSCRIPTION = "new_subscription"
    EVENT_RENEWAL = "renewal"
    EVENT_UPGRADE = "upgrade"
    EVENT_DOWNGRADE = "downgrade"
    EVENT_CANCELLATION = "cancellation"
    EVENT_CHURN = "churn"
    EVENT_REACTIVATION = "reactivation"
    EVENT_MANUAL_ADJUSTMENT = "manual_adjustment"
    EVENT_TYPE_CHOICES = (
        (EVENT_NEW_SUBSCRIPTION, "New subscription"),
        (EVENT_RENEWAL, "Renewal"),
        (EVENT_UPGRADE, "Upgrade"),
        (EVENT_DOWNGRADE, "Downgrade"),
        (EVENT_CANCELLATION, "Cancellation"),
        (EVENT_CHURN, "Churn"),
        (EVENT_REACTIVATION, "Reactivation"),
        (EVENT_MANUAL_ADJUSTMENT, "Manual adjustment"),
    )

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="revenue_events")
    subscription = models.ForeignKey("billing.OrganizationSubscription", on_delete=models.SET_NULL, null=True, blank=True, related_name="revenue_events")
    plan = models.ForeignKey("billing.Plan", on_delete=models.SET_NULL, null=True, blank=True, related_name="revenue_events")
    event_type = models.CharField(max_length=24, choices=EVENT_TYPE_CHOICES)
    billing_interval = models.CharField(max_length=20, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="PLN")
    mrr_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    arr_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    effective_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_revenue_events")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_at", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "effective_at"]),
            models.Index(fields=["event_type", "effective_at"]),
            models.Index(fields=["effective_at"]),
            models.Index(fields=["billing_interval"]),
        ]

    def __str__(self):
        return f"{self.organization}: {self.event_type}"


class SalesAccount(models.Model):
    STATUS_PROSPECT = "prospect"
    STATUS_ACTIVE_CUSTOMER = "active_customer"
    STATUS_CHURNED = "churned"
    STATUS_NOT_FIT = "not_fit"
    STATUS_CHOICES = (
        (STATUS_PROSPECT, "Prospect"),
        (STATUS_ACTIVE_CUSTOMER, "Active customer"),
        (STATUS_CHURNED, "Churned"),
        (STATUS_NOT_FIT, "Not a fit"),
    )

    name = models.CharField(max_length=255)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_accounts")
    franchise = models.ForeignKey("franchises.Franchise", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_accounts")
    website_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROSPECT)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_sales_accounts")
    source = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_follow_up_at", "name"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["next_follow_up_at"]),
            models.Index(fields=["last_activity_at"]),
        ]

    def __str__(self):
        return self.name


class SalesContact(models.Model):
    account = models.ForeignKey(SalesAccount, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        indexes = [models.Index(fields=["account", "is_primary"]), models.Index(fields=["email"])]

    def __str__(self):
        return f"{self.name} ({self.account})"


class SalesOpportunity(models.Model):
    STAGE_PROSPECTING = "prospecting"
    STAGE_CONTACTED = "contacted"
    STAGE_DISCOVERY = "discovery"
    STAGE_DEMO = "demo"
    STAGE_PROPOSAL = "proposal"
    STAGE_NEGOTIATION = "negotiation"
    STAGE_WON = "won"
    STAGE_LOST = "lost"
    STAGE_CHURN_RISK = "churn_risk"
    STAGE_CHURNED = "churned"
    STAGE_CHOICES = (
        (STAGE_PROSPECTING, "Prospecting"), (STAGE_CONTACTED, "Contacted"),
        (STAGE_DISCOVERY, "Discovery"), (STAGE_DEMO, "Demo"),
        (STAGE_PROPOSAL, "Proposal"), (STAGE_NEGOTIATION, "Negotiation"),
        (STAGE_WON, "Won"), (STAGE_LOST, "Lost"),
        (STAGE_CHURN_RISK, "Churn risk"), (STAGE_CHURNED, "Churned"),
    )

    account = models.ForeignKey(SalesAccount, on_delete=models.CASCADE, related_name="opportunities")
    organization = models.ForeignKey("accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_opportunities")
    franchise = models.ForeignKey("franchises.Franchise", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales_opportunities")
    title = models.CharField(max_length=255)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_PROSPECTING)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_sales_opportunities")
    expected_monthly_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_annual_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    probability = models.PositiveIntegerField(default=10)
    expected_close_date = models.DateField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)
    churn_reason = models.CharField(max_length=255, blank=True)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    won_at = models.DateTimeField(null=True, blank=True)
    lost_at = models.DateTimeField(null=True, blank=True)
    churned_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_follow_up_at", "-updated_at"]
        indexes = [
            models.Index(fields=["stage"]), models.Index(fields=["assigned_to", "stage"]),
            models.Index(fields=["next_follow_up_at"]), models.Index(fields=["expected_close_date"]),
            models.Index(fields=["last_activity_at"]),
        ]

    @property
    def weighted_monthly_value(self):
        return (self.expected_monthly_value * Decimal(self.probability)) / Decimal("100")

    def __str__(self):
        return self.title


class SalesActivity(models.Model):
    TYPE_CALL = "call"
    TYPE_EMAIL = "email"
    TYPE_MEETING = "meeting"
    TYPE_NOTE = "note"
    TYPE_TASK = "task"
    TYPE_DEMO = "demo"
    TYPE_PROPOSAL_SENT = "proposal_sent"
    TYPE_CONTRACT_SENT = "contract_sent"
    TYPE_NEGOTIATION = "negotiation"
    TYPE_STATUS_CHANGE = "status_change"
    ACTIVITY_TYPE_CHOICES = (
        (TYPE_CALL, "Call"), (TYPE_EMAIL, "Email"), (TYPE_MEETING, "Meeting"),
        (TYPE_NOTE, "Note"), (TYPE_TASK, "Task"), (TYPE_DEMO, "Demo"),
        (TYPE_PROPOSAL_SENT, "Proposal sent"), (TYPE_CONTRACT_SENT, "Contract sent"),
        (TYPE_NEGOTIATION, "Negotiation"), (TYPE_STATUS_CHANGE, "Status change"),
    )

    account = models.ForeignKey(SalesAccount, on_delete=models.CASCADE, related_name="activities")
    opportunity = models.ForeignKey(SalesOpportunity, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities")
    contact = models.ForeignKey(SalesContact, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities")
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    old_stage = models.CharField(max_length=50, blank=True)
    new_stage = models.CharField(max_length=50, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_sales_activities")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account", "created_at"]), models.Index(fields=["opportunity", "created_at"]),
            models.Index(fields=["activity_type", "created_at"]), models.Index(fields=["due_at"]),
            models.Index(fields=["completed_at"]), models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return self.subject or self.get_activity_type_display()
