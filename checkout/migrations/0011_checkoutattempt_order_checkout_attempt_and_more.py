import django.db.models.deletion
import uuid

from django.db import migrations, models
from django.db.models import Q


def ensure_unique_nonempty_stripe_payment_ids(apps, schema_editor):
    Order = apps.get_model("checkout", "Order")
    duplicates = list(
        Order.objects.exclude(stripe_pid="")
        .values("stripe_pid")
        .annotate(order_count=models.Count("id"))
        .filter(order_count__gt=1)
        .values_list("stripe_pid", flat=True)[:10]
    )
    if duplicates:
        raise RuntimeError(
            "Cannot enforce unique Stripe PaymentIntent IDs because duplicate "
            f"orders exist for: {', '.join(duplicates)}. Reconcile those orders "
            "before rerunning this migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("checkout", "0010_order_discount_fields_and_redemptions"),
        ("userprofiles", "0005_auth_user_email_ci_trim_unique_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="CheckoutAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("checkout_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("payment_intent_id", models.CharField(blank=True, max_length=255, null=True, unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("cart_snapshot", models.JSONField(default=list)),
                ("pricing_snapshot", models.JSONField(default=list)),
                ("shipping_details_snapshot", models.JSONField(blank=True, default=dict)),
                ("shipping_method", models.CharField(default="budget", max_length=20)),
                ("customer_email", models.EmailField(max_length=254)),
                ("save_info", models.BooleanField(default=False)),
                ("shipping_cost", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("free_shipping_applied", models.BooleanField(default=False)),
                ("discount_code", models.CharField(blank=True, default="", max_length=50)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("discount_percent", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("discount_label", models.CharField(blank=True, default="", max_length=100)),
                ("expected_amount_cents", models.PositiveBigIntegerField()),
                ("currency", models.CharField(default="eur", max_length=3)),
                ("terms_accepted_at", models.DateTimeField(blank=True, null=True)),
                ("terms_version", models.CharField(blank=True, default="", max_length=80)),
                ("privacy_accepted_at", models.DateTimeField(blank=True, null=True)),
                ("privacy_version", models.CharField(blank=True, default="", max_length=80)),
                ("personal_use_accepted_at", models.DateTimeField(blank=True, null=True)),
                ("personal_terms_version", models.CharField(blank=True, default="", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user_profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="checkout_attempts", to="userprofiles.userprofile")),
            ],
        ),
        migrations.AddField(
            model_name="order",
            name="checkout_attempt",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="order", to="checkout.checkoutattempt"),
        ),
        migrations.AddField(
            model_name="order",
            name="prodigi_submission_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="fulfilment_hold_reason",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AlterField(
            model_name="order",
            name="confirmation_email_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("SENDING", "Sending"),
                    ("SENT", "Sent"),
                    ("FAILED", "Failed"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            ensure_unique_nonempty_stripe_payment_ids,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(condition=~Q(stripe_pid=""), fields=("stripe_pid",), name="uniq_order_nonempty_stripe_pid"),
        ),
    ]
