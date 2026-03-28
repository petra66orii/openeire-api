from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from blog.models import BlogPost
from products.models import Photo, PrintTemplate


@override_settings(FRONTEND_URL="https://openeire.ie")
class SiteMetadataTests(TestCase):
    def test_robots_txt_disallows_all_crawling(self):
        response = self.client.get(reverse("robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("User-agent: *", response.content.decode())
        self.assertIn("Disallow: /", response.content.decode())
        self.assertIn("/sitemap.xml", response.content.decode())

    def test_sitemap_index_lists_content_sections(self):
        response = self.client.get(reverse("sitemap_index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        content = response.content.decode()
        self.assertIn("sitemap-static.xml", content)
        self.assertIn("sitemap-blog.xml", content)
        self.assertIn("sitemap-physical.xml", content)

    def test_static_sitemap_lists_indexable_frontend_pages(self):
        response = self.client.get(reverse("sitemap_section", args=["static"]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("https://openeire.ie/", content)
        self.assertIn("https://openeire.ie/blog", content)
        self.assertIn("https://openeire.ie/gallery/physical", content)
        self.assertNotIn("https://openeire.ie/gallery/photo/", content)
        self.assertNotIn("https://openeire.ie/gallery/video/", content)
        self.assertIn("<priority>1.0</priority>", content)
        self.assertIn("<priority>0.4</priority>", content)
        self.assertNotIn("bound method", content)

    def test_blog_sitemap_uses_frontend_blog_urls(self):
        author = get_user_model().objects.create_user(
            username="blogauthor",
            email="blogauthor@example.com",
            password="StrongPass123!",
        )
        BlogPost.objects.create(
            title="Sitemap Blog Post",
            author=author,
            content="<p>Published content</p>",
            excerpt="Excerpt",
            status=1,
        )

        response = self.client.get(reverse("sitemap_section", args=["blog"]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("https://openeire.ie/blog/sitemap-blog-post", content)

    def test_physical_sitemap_uses_frontend_gallery_urls(self):
        PrintTemplate.objects.create(
            material="eco_canvas",
            size="12x18",
            production_cost=Decimal("40.00"),
            sku_suffix="CAN-12x18",
        )
        preview = SimpleUploadedFile("preview.jpg", b"preview", content_type="image/jpeg")
        high_res = SimpleUploadedFile("high_res.jpg", b"high_res", content_type="image/jpeg")
        photo = Photo.objects.create(
            title="Digital Photo",
            description="Digital description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price=Decimal("20.00"),
            is_active=True,
            is_printable=True,
        )
        physical_response = self.client.get(reverse("sitemap_section", args=["physical"]))

        self.assertIn(f"https://openeire.ie/gallery/physical/{photo.id}", physical_response.content.decode())
