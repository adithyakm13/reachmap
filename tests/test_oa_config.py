"""M16 OpenAPI: the configuration. `http_server = { kind = "openapi", spec = ..., handlers = ... }` on a server repository and
`http_client = { kind = "openapi", spec = ..., client = ... }` on a client repository, checked when the config loads: every
problem names its key and none quotes a configured value."""

import unittest

from reachmap import config

HEAD = '''
workspace = "t"

[repos."t/api"]
host = "git"
remote = "/nowhere/api"
language = "python"
roles = ["backend"]
{api}

[repos."t/web"]
host = "git"
remote = "/nowhere/web"
language = "typescript"
roles = ["frontend"]
{web}

[[edges]]
kind = "http"
from = "t/web"
to = "t/api"
'''

API_OK = 'http_server = { kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }'
WEB_OK = 'http_client = { kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }'


def load(api=API_OK, web=WEB_OK):
    return config.loads(HEAD.format(api=api, web=web), fmt="toml", env={})


def problems(api=API_OK, web=WEB_OK):
    try:
        load(api, web)
    except config.ConfigError as exc:
        return [(p.path, p.message) for p in exc.problems]
    return []


class Loads(unittest.TestCase):
    def test_both_sections_load_and_are_kept_as_written(self):
        ws = load()
        self.assertEqual(dict(ws.repos["t/api"].http_server), {"kind": "openapi", "spec": "api/openapi.json", "handlers": "app/handlers/*.py"})
        self.assertEqual(dict(ws.repos["t/web"].http_client), {"kind": "openapi", "spec": "api/openapi.json", "client": "src/api/generated"})
        self.assertIsNone(ws.repos["t/web"].http_server)
        self.assertIsNone(ws.repos["t/api"].http_client)

    def test_a_repository_without_the_section_has_none(self):
        ws = load(api="", web="")
        self.assertIsNone(ws.repos["t/api"].http_server)
        self.assertIsNone(ws.repos["t/web"].http_client)

    def test_handlers_and_client_may_be_lists(self):
        ws = load(api='http_server = { kind = "openapi", spec = "s.json", handlers = ["a/*.py", "b/**/*.py"] }',
                  web='http_client = { kind = "openapi", spec = "s.json", client = ["src/gen/a.ts", "src/gen/b"] }')
        self.assertEqual(list(ws.repos["t/api"].http_server["handlers"]), ["a/*.py", "b/**/*.py"])
        self.assertEqual(list(ws.repos["t/web"].http_client["client"]), ["src/gen/a.ts", "src/gen/b"])

    def test_the_other_kinds_of_http_client_still_load(self):
        ws = load(web='http_client = { kind = "endpoints-file", path = "src/api/endpoints.ts" }')
        self.assertEqual(ws.repos["t/web"].http_client["kind"], "endpoints-file")

    def test_a_celery_entry_points_goes_with_a_spec_because_it_has_no_routes(self):
        ws = load(api='entry_points = { kind = "celery" }\nhttp_server = { kind = "openapi", spec = "api/openapi.json" }')
        self.assertEqual(ws.repos["t/api"].entry_points["kind"], "celery")
        self.assertEqual(ws.repos["t/api"].http_server["kind"], "openapi")


class Refusals(unittest.TestCase):
    def one(self, api=API_OK, web=WEB_OK):
        found = problems(api, web)
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_a_spec_is_required(self):
        path, message = self.one(api='http_server = { kind = "openapi" }')
        self.assertEqual(path, 'repos."t/api".http_server')
        self.assertIn("spec: missing", message)
        path, message = self.one(web='http_client = { kind = "openapi", client = "src/gen" }')
        self.assertEqual(path, 'repos."t/web".http_client')
        self.assertIn("spec: missing", message)

    def test_entry_points_with_routes_and_a_spec_are_two_sources_of_routes_and_refused(self):
        for kind in ("fastapi", "react-router"):
            with self.subTest(kind=kind):
                path, message = self.one(api=f'entry_points = {{ kind = "{kind}" }}\nhttp_server = {{ kind = "openapi", spec = "x.json" }}')
                self.assertEqual(path, 'repos."t/api".http_server')
                self.assertIn("one source of routes", message)
                self.assertIn(kind, message)

    def test_the_generated_client_is_required_on_the_client_side(self):
        path, message = self.one(web='http_client = { kind = "openapi", spec = "api/openapi.json" }')
        self.assertIn("client: missing", message)

    def test_an_unknown_kind_names_the_known_ones(self):
        path, message = self.one(api='http_server = { kind = "swagger", spec = "x" }')
        self.assertEqual(path, 'repos."t/api".http_server.kind')
        self.assertIn("openapi", message)

    def test_the_kind_is_required_on_the_server_side(self):
        path, message = self.one(api='http_server = { spec = "x.json" }')
        self.assertEqual(path, 'repos."t/api".http_server.kind')

    def test_a_key_that_is_not_one_is_named_with_the_near_miss(self):
        _path, message = self.one(api='http_server = { kind = "openapi", spec = "x.json", handler = "a/*.py" }')
        self.assertIn("handler", message)
        self.assertIn("handlers", message)
        _path, message = self.one(web='http_client = { kind = "openapi", spec = "x.json", client = "g", pth = "x" }')
        self.assertIn("pth", message)

    def test_a_spec_is_a_path_inside_the_repository(self):
        for bad in ('"/etc/openapi.json"', '"../openapi.json"', '"a/../../b.json"', '"a\\\\b.json"', '""', "3", '["a.json"]'):
            with self.subTest(spec=bad):
                _path, message = self.one(api=f'http_server = {{ kind = "openapi", spec = {bad} }}')
                self.assertTrue(message.startswith("spec:"), message)

    def test_a_yaml_spec_is_refused_when_the_config_loads(self):
        for name in ("api/openapi.yaml", "api/openapi.yml", "API/OPENAPI.YAML"):
            with self.subTest(spec=name):
                path, message = self.one(api=f'http_server = {{ kind = "openapi", spec = "{name}" }}')
                self.assertEqual(path, 'repos."t/api".http_server')
                self.assertIn("YAML is not read", message)
                self.assertIn("commit a JSON copy", message)
                self.assertNotIn(name, message)
                path, message = self.one(web=f'http_client = {{ kind = "openapi", spec = "{name}", client = "src/gen" }}')
                self.assertEqual(path, 'repos."t/web".http_client')
                self.assertIn("YAML is not read", message)

    def test_handlers_and_client_are_paths_or_globs_inside_the_repository(self):
        for bad in ('"/abs/*.py"', '"../x/*.py"', "3", "[1]", '[""]'):
            with self.subTest(handlers=bad):
                _path, message = self.one(api=f'http_server = {{ kind = "openapi", spec = "s.json", handlers = {bad} }}')
                self.assertTrue(message.startswith("handlers:"), message)
        for bad in ('"/abs/gen"', '"../gen"', "3", "[]", '[""]'):
            with self.subTest(client=bad):
                _path, message = self.one(web=f'http_client = {{ kind = "openapi", spec = "s.json", client = {bad} }}')
                self.assertTrue(message.startswith("client:"), message)

    def test_every_problem_is_reported_not_only_the_first(self):
        found = problems(api='http_server = { kind = "openapi", spec = 3, handlers = 4 }', web='http_client = { kind = "openapi" }')
        self.assertEqual(len(found), 4)

    def test_no_message_quotes_a_value(self):
        secret = "not-a-real-secret-value-417"
        for api in (f'http_server = {{ kind = "openapi", spec = "../{secret}.json" }}', f'http_server = {{ kind = "openapi", spec = "x.json", handlers = ["{secret}", 3] }}',
                    f'http_server = {{ kind = "openapi", spec = {{ a = "{secret}" }} }}'):
            with self.subTest(api=api[:60]):
                found = problems(api=api)
                self.assertTrue(found)
                for path, message in found:
                    self.assertNotIn(secret, path + message)


if __name__ == "__main__":
    unittest.main()
