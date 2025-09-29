from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

class BlogPost(models.Model):
    """
    Stores a single blog post entry.
    """
    STATUS = ((0, 'Draft'), (1, 'Published'))

    title = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blog_posts')
    featured_image = models.ImageField(upload_to='blog_images/', null=True, blank=True)
    content = models.TextField()
    excerpt = models.TextField(blank=True)
    status = models.IntegerField(choices=STATUS, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """
        Generate a unique slug from the title if one is not provided.
        """
        if not self.slug:
            self.slug = slugify(self.title)
            # Ensure the slug is unique
            unique_slug = self.slug
            num = 1
            while BlogPost.objects.filter(slug=unique_slug).exists():
                unique_slug = f'{self.slug}-{num}'
                num += 1
            self.slug = unique_slug
        super().save(*args, **kwargs)

class Comment(models.Model):
    """
    Stores a single comment entry related to a blog post.
    """
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blog_comments')
    content = models.TextField()
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment by {self.user.username} on {self.post.title}'