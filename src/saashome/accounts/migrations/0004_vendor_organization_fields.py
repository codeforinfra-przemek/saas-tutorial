from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_create_missing_user_profiles"),
    ]

    operations = [
        migrations.RenameField(
            model_name="organization",
            old_name="website",
            new_name="website_url",
        ),
        migrations.AddField(
            model_name="organization",
            name="contact_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="organization",
            name="billing_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="organization",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("inactive", "Inactive"),
                    ("suspended", "Suspended"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="package_type",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("basic", "Basic"),
                    ("premium", "Premium"),
                    ("enterprise", "Enterprise"),
                ],
                default="free",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="organizationmembership",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="organizationmembership",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
