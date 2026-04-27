from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0007_order_prodigi_tracking_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='confirmation_email_error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='order',
            name='confirmation_email_failed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='confirmation_email_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='confirmation_email_status',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('SENT', 'Sent'), ('FAILED', 'Failed')],
                default='PENDING',
                max_length=20,
            ),
        ),
    ]
