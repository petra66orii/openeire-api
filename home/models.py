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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email