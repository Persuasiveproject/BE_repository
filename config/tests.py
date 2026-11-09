from django.http import JsonResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path


def cors_probe(request):
    return JsonResponse({'data': {'ok': True}}, status=401 if request.GET.get('denied') else 200)


urlpatterns = [path('v1/cors-probe', cors_probe), path('health/', cors_probe)]


@override_settings(
    ROOT_URLCONF=__name__,
    CORS_ALLOWED_ORIGINS=['http://localhost:8082'],
)
class BrowserCorsTests(SimpleTestCase):
    def test_allowed_origin_can_read_json(self):
        response = self.client.get('/v1/cors-probe', HTTP_ORIGIN='http://localhost:8082')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:8082')
        self.assertIn('origin', response['Vary'].lower())
        self.assertNotIn('Access-Control-Allow-Credentials', response)

    def test_preflight_allows_json_auth_and_idempotency(self):
        for method in ['POST', 'PATCH']:
            with self.subTest(method=method):
                response = self.client.options(
                    '/v1/cors-probe', HTTP_ORIGIN='http://localhost:8082',
                    HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
                    HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type,idempotency-key',
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:8082')
                self.assertIn(method, response['Access-Control-Allow-Methods'])
                headers = response['Access-Control-Allow-Headers'].split(', ')
                for header in ['authorization', 'content-type', 'idempotency-key']:
                    self.assertIn(header, headers)

    def test_other_origins_are_not_allowed(self):
        for origin in ['https://untrusted.example', 'http://localhost:8083', 'https://localhost:8082', 'null']:
            with self.subTest(origin=origin):
                response = self.client.get('/v1/cors-probe', HTTP_ORIGIN=origin)
                self.assertNotIn('Access-Control-Allow-Origin', response)
                preflight = self.client.options('/v1/cors-probe', HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST')
                self.assertNotIn('Access-Control-Allow-Origin', preflight)

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_empty_allowlist_disables_cors(self):
        response = self.client.get('/v1/cors-probe', HTTP_ORIGIN='http://localhost:8082')
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_api_errors_keep_cors_headers(self):
        response = self.client.get('/v1/cors-probe?denied=1', HTTP_ORIGIN='http://localhost:8082')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:8082')

    def test_non_api_paths_are_not_exposed(self):
        response = self.client.get('/health/', HTTP_ORIGIN='http://localhost:8082')
        self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_requests_without_origin_keep_working(self):
        response = self.client.get('/v1/cors-probe')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Access-Control-Allow-Origin', response)
