from django.db import models

class Testimonial(models.Model):
    """
    Model to represent a client testimonial.
    """
    name = models.CharField(max_length=100)
    text = models.TextField()
    rating = models.PositiveIntegerField(default=5)

    def __str__(self):
        return f"Testimonial by {self.name}"


class NewsletterSubscriber(models.Model):
    """
    Model to store emails of users who sign up for the newsletter.
    """
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=50, blank=True, default="")
    brevo_synced_at = models.DateTimeField(null=True, blank=True)
    brevo_sync_status = models.CharField(max_length=20, blank=True, default="")
    brevo_sync_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.email = str(self.email or "").strip().lower()
        self.first_name = str(self.first_name or "").strip()
        self.source = str(self.source or "").strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
