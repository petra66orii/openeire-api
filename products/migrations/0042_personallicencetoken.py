from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0008_order_confirmation_email_fields'),
        ('products', '0041_galleryaccess_granted_user_galleryaccess_verified_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonalLicenceToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_licence_tokens', to='checkout.order')),
            ],
            options={
                'verbose_name': 'Personal Licence Token',
                'verbose_name_plural': 'Personal Licence Tokens',
                'ordering': ['-created_at'],
            },
        ),
    ]
