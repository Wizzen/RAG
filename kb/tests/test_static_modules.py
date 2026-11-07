from django.contrib.staticfiles.views import serve
from django.test import RequestFactory, SimpleTestCase


class StaticModuleTests(SimpleTestCase):
    def test_workbench_modules_are_served_as_javascript(self):
        for path in (
            'operations_workbench/workbench.mjs',
            'operations_workbench/presentation.mjs',
            'vehicle_tracker/js/equipment-library.mjs',
            'vehicle_tracker/js/equipment-model.mjs',
        ):
            with self.subTest(path=path):
                response = serve(RequestFactory().get('/static/' + path), path, insecure=True)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response['Content-Type'].split(';')[0], 'application/javascript')
                finally:
                    response.close()
