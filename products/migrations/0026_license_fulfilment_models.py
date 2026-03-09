from django.db import migrations, models
import django.db.models.deletion
import uuid

import products.storage


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0025_license_request_active_constraint_ci'),
    ]

    operations = [
        migrations.CreateModel(
            name='StripeWebhookEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stripe_event_id', models.CharField(max_length=255, unique=True)),
                ('event_type', models.CharField(max_length=255)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('SUCCESS', 'Success'), ('FAILED', 'Failed')], max_length=20)),
                ('error_message', models.TextField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Stripe Webhook Event',
                'verbose_name_plural': 'Stripe Webhook Events',
                'ordering': ['-received_at'],
            },
        ),
        migrations.CreateModel(
            name='LicenceDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('doc_type', models.CharField(choices=[('SCHEDULE', 'Appendix A - Licence Schedule'), ('CERTIFICATE', 'Appendix B - Licence Certificate')], max_length=20)),
                ('file', models.FileField(storage=products.storage.PrivateAssetStorage(), upload_to='licences/documents/')),
                ('sha256', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('license_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='licence_documents', to='products.licenserequest')),
            ],
            options={
                'verbose_name': 'Licence Document',
                'verbose_name_plural': 'Licence Documents',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LicenceDeliveryToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('license_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delivery_tokens', to='products.licenserequest')),
            ],
            options={
                'verbose_name': 'Licence Delivery Token',
                'verbose_name_plural': 'Licence Delivery Tokens',
                'ordering': ['-created_at'],
            },
        ),
    ]
