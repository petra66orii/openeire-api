from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0002_blogpost_likes_blogpost_tags'),
    ]

    operations = [
        migrations.AddField(
            model_name='blogpost',
            name='canonical_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='blogpost',
            name='meta_description',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='blogpost',
            name='meta_title',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
