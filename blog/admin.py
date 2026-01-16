from django.contrib import admin
from django_summernote.admin import SummernoteModelAdmin
from .models import BlogPost, Comment
from openeire_api.admin import custom_admin_site
from django.contrib.auth import get_user_model

User = get_user_model()

@admin.register(BlogPost, site=custom_admin_site)
class BlogPostAdmin(SummernoteModelAdmin):
    """
    Admin configuration for the BlogPost model.
    """
    list_display = ('title', 'slug', 'author', 'status', 'created_at')
    list_filter = ('status', 'created_at', 'author', 'tags')
    search_fields = ['title', 'content']
    prepopulated_fields = {'slug': ('title',)}
    summernote_fields = ('content',)
    readonly_fields = ('likes_count_display',) 
    exclude = ('likes',) 

    def likes_count_display(self, obj):
        return obj.number_of_likes()
    likes_count_display.short_description = 'Total Likes'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "author":
            # Only show users where is_superuser=True
            kwargs["queryset"] = User.objects.filter(is_superuser=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Comment, site=custom_admin_site)
class CommentAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Comment model.
    """
    list_display = ('user', 'content', 'post', 'created_at', 'approved')
    list_filter = ('approved', 'created_at')
    search_fields = ('user__username', 'content')
    actions = ['approve_comments']

    def approve_comments(self, request, queryset):
        queryset.update(approved=True)
    approve_comments.short_description = "Approve selected comments"