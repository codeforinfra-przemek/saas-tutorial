from django.test import RequestFactory, TestCase
from django.urls import reverse

from franchises.models import Franchise, FranchiseCategory
from seo.services import seo_context


class SeoPageTests(TestCase):
    def setUp(self):
        self.category = FranchiseCategory.objects.create(name="SEO Gastronomia", slug="seo-test-gastronomia")
        self.franchise = Franchise.objects.create(
            name="Testowa Marka",
            slug="seo-testowa-marka",
            category=self.category,
            short_description="Profil do testow SEO.",
            min_investment=40000,
            business_type=Franchise.BUSINESS_TYPE_STATIONARY,
        )

    def test_public_seo_pages_render_with_canonical_metadata(self):
        urls = (
            self.franchise.get_absolute_url(),
            self.category.get_absolute_url(),
            reverse("seo:budget_detail", kwargs={"slug": "do-100000-zl"}),
            reverse("seo:model_detail", kwargs={"slug": "stacjonarna"}),
            reverse("seo:methodology"),
            reverse("seo:how_it_works"),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'rel="canonical"')

    def test_listing_query_is_noindex_and_uses_clean_canonical(self):
        response = self.client.get(reverse("franchises:list"), {"q": "test"})
        self.assertEqual(response.context["robots_meta"], "noindex,follow")
        self.assertNotIn("?q=test", response.context["canonical_url"])

    def test_private_paths_receive_noindex_defaults(self):
        request = RequestFactory().get("/vendor/")
        self.assertEqual(seo_context(request)["robots_meta"], "noindex,nofollow")
