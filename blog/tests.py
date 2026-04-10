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

    def test_blog_post_parses_comma_separated_image_allowlist_string(self):
        with self.settings(BLOG_ALLOWED_IMAGE_HOSTS='images.example.com, cdn.example.org'):
            post = self._create_post(
                title='Comma Separated Allowlist',
                content=(
                    '<img src="https://images.example.com/ok.jpg">'
                    '<img src="https://tracker.s/blocked.jpg">'
                ),
            )

        self.assertIn('src="https://images.example.com/ok.jpg"', post.content)
        self.assertNotIn('tracker.s', post.content)

    def test_blog_post_excerpt_is_sanitized_to_plain_text(self):
        post = self._create_post(
            title='Excerpt Sanitization',
            content='<p>safe</p>',
            excerpt='<b>Quick</b> summary <script>alert(1)</script>',
        )

        self.assertEqual(post.excerpt, 'Quick summary')

    def test_blog_post_generates_slug_only_when_blank(self):
        post = self._create_post(
            title='SEO Slug Post',
            content='<p>Body</p>',
        )

        self.assertEqual(post.slug, 'seo-slug-post')

        post.title = 'Updated SEO Slug Post'
        post.save()

        self.assertEqual(post.slug, 'seo-slug-post')

    def test_blog_list_exposes_seo_fields(self):
        post = self._create_post(
            title='SEO List Post',
            content='<p>Body</p>',
            excerpt='List excerpt',
        )
        post.meta_title = 'List Meta Title'
        post.meta_description = 'List meta description'
        post.canonical_url = 'https://openeire.ie/blog/seo-list-post/'
        post.save()

        response = self.client.get(reverse('blog_post_list'))

        self.assertEqual(response.status_code, 200)
        results = self._list_results(response)
        self.assertEqual(results[0]['meta_title'], 'List Meta Title')
        self.assertEqual(results[0]['meta_description'], 'List meta description')
        self.assertEqual(
            results[0]['canonical_url'],
            'https://openeire.ie/blog/seo-list-post/',
        )

    def test_blog_detail_exposes_seo_fields_and_excerpt(self):
        post = self._create_post(
            title='SEO Detail Post',
            content='<p>Body</p>',
            excerpt='Detail excerpt',
        )
        post.meta_title = 'Detail Meta Title'
        post.meta_description = 'Detail meta description'
        post.canonical_url = 'https://openeire.ie/blog/seo-detail-post/'
        post.save()

        response = self.client.get(reverse('blog_post_detail', args=[post.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['excerpt'], 'Detail excerpt')
        self.assertEqual(response.data['meta_title'], 'Detail Meta Title')
        self.assertEqual(response.data['meta_description'], 'Detail meta description')
        self.assertEqual(
            response.data['canonical_url'],
            'https://openeire.ie/blog/seo-detail-post/',
        )

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

    def test_comment_post_accepts_writable_content_and_sanitizes(self):
        post = self._create_post(
            title='Comment Create API',
            content='<p>Body</p>',
        )
        self.client.force_authenticate(user=self.author)

        response = self.client.post(
            reverse('comment_list_create', args=[post.slug]),
            {'content': '<b>Great</b> <script>alert(1)</script>'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        created = Comment.objects.get(post=post, user=self.author)
        self.assertEqual(created.content, 'Great')
        self.assertEqual(response.data['content'], 'Great')
