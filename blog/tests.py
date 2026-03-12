from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import BlogPost, Comment


class BlogSanitizationTests(APITestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            username='blogauthor',
            email='blogauthor@example.com',
            password='StrongPass123!',
        )

    def _create_post(self, *, title, content, excerpt=''):
        return BlogPost.objects.create(
            title=title,
            author=self.author,
            content=content,
            excerpt=excerpt,
            status=1,
        )

    def _list_results(self, response):
        if isinstance(response.data, dict) and 'results' in response.data:
            return response.data['results']
        return response.data

    def test_blog_post_save_sanitizes_unsafe_html(self):
        post = self._create_post(
            title='Unsafe Blog Post',
            content=(
                '<p>Hello <strong>world</strong></p>'
                '<script>alert(1)</script>'
                '<a href="javascript:alert(1)" onclick="evil()">Click</a>'
                '<img src="https://example.com/pic.jpg" onerror="boom()" />'
            ),
        )

        cleaned = post.content
        self.assertIn('<p>Hello <strong>world</strong></p>', cleaned)
        self.assertIn('<a>Click</a>', cleaned)
        self.assertNotIn('<script', cleaned.lower())
        self.assertNotIn('javascript:', cleaned.lower())
        self.assertNotIn('onclick=', cleaned.lower())
        self.assertNotIn('onerror=', cleaned.lower())
        self.assertNotIn('src="https://example.com/pic.jpg"', cleaned)

    def test_blog_post_allows_image_host_from_allowlist(self):
        with self.settings(BLOG_ALLOWED_IMAGE_HOSTS=['images.example.com']):
            post = self._create_post(
                title='Allowed Image Host',
                content='<p>Image</p><img src="https://images.example.com/photo.jpg" alt="img">',
            )

        self.assertIn('src="https://images.example.com/photo.jpg"', post.content)

    def test_blog_post_excerpt_is_sanitized_to_plain_text(self):
        post = self._create_post(
            title='Excerpt Sanitization',
            content='<p>safe</p>',
            excerpt='<b>Quick</b> summary <script>alert(1)</script>',
        )

        self.assertEqual(post.excerpt, 'Quick summary')

    def test_comment_save_sanitizes_html_and_scripts(self):
        post = self._create_post(
            title='Comment Sanitization',
            content='<p>Body</p>',
        )
        comment = Comment.objects.create(
            post=post,
            user=self.author,
            content='<img src=x onerror=alert(1)> Nice post <script>alert(1)</script>',
        )

        self.assertEqual(comment.content, 'Nice post')

    def test_blog_detail_sanitizes_legacy_unsanitized_content_on_read(self):
        post = self._create_post(
            title='Legacy Content Post',
            content='<p>Initial</p>',
        )
        BlogPost.objects.filter(pk=post.pk).update(
            content='<script>alert(1)</script><p>Visible</p>'
        )

        response = self.client.get(reverse('blog_post_detail', args=[post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['content'], '<p>Visible</p>')

    def test_blog_list_sanitizes_legacy_unsanitized_excerpt_on_read(self):
        post = self._create_post(
            title='Legacy Excerpt Post',
            content='<p>Body</p>',
            excerpt='safe excerpt',
        )
        BlogPost.objects.filter(pk=post.pk).update(
            excerpt='<img src=x onerror=alert(1)> teaser <script>boom()</script>'
        )

        response = self.client.get(reverse('blog_post_list'))

        self.assertEqual(response.status_code, 200)
        results = self._list_results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['excerpt'], 'teaser')

    def test_comment_list_sanitizes_legacy_unsanitized_comment_on_read(self):
        post = self._create_post(
            title='Legacy Comment Post',
            content='<p>Body</p>',
        )
        comment = Comment.objects.create(
            post=post,
            user=self.author,
            content='safe comment',
            approved=True,
        )
        Comment.objects.filter(pk=comment.pk).update(
            content='<script>alert(1)</script><b>Hello</b>'
        )

        response = self.client.get(reverse('comment_list_create', args=[post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['content'], 'Hello')
