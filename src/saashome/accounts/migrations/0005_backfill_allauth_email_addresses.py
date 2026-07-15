from django.conf import settings
from django.db import migrations


def backfill_email_addresses(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    UserProfile = apps.get_model("accounts", "UserProfile")
    EmailAddress = apps.get_model("account", "EmailAddress")

    profiles = {
        profile.user_id: profile
        for profile in UserProfile.objects.only("user_id", "email_verified")
    }

    for user in User.objects.exclude(email="").only("id", "email", "is_active"):
        email = user.email.strip().lower()
        if not email:
            continue

        existing_for_user = EmailAddress.objects.filter(
            user_id=user.id,
            email__iexact=email,
        ).first()
        profile = profiles.get(user.id)
        should_be_verified = user.is_active or bool(
            profile and profile.email_verified
        )

        if existing_for_user:
            changed_fields = []
            if should_be_verified and not existing_for_user.verified:
                existing_for_user.verified = True
                changed_fields.append("verified")
            if not EmailAddress.objects.filter(
                user_id=user.id,
                primary=True,
            ).exclude(id=existing_for_user.id).exists() and not existing_for_user.primary:
                existing_for_user.primary = True
                changed_fields.append("primary")
            if changed_fields:
                existing_for_user.save(update_fields=changed_fields)
            continue

        if EmailAddress.objects.filter(email__iexact=email).exists():
            continue

        EmailAddress.objects.create(
            user_id=user.id,
            email=email,
            verified=should_be_verified,
            primary=not EmailAddress.objects.filter(
                user_id=user.id,
                primary=True,
            ).exists(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("account", "0009_emailaddress_unique_primary_email"),
        ("accounts", "0004_vendor_organization_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_email_addresses, migrations.RunPython.noop),
    ]
