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
