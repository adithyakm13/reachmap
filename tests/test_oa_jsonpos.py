"""M16 OpenAPI: the JSON reader that keeps the line of every key and value (a spec line is the evidence of an operation).

The reader is strict (RFC 8259), refuses what would make a document ambiguous (a duplicate key, a nesting deeper than the limit)
and never answers "empty" for text it could not read: every failure is a `JsonError` with a line.
"""

import json
import unittest

from reachmap.adapters.openapi.jsonpos import JsonError, MAX_DEPTH, parse

DOC = '''{
  "openapi": "3.0.3",
  "info": {
    "title": "Ledger",
    "version": "1"
  },
  "paths": {
    "/invoices": {
      "get": {
        "operationId": "listInvoices"
      }
    }
  },
  "tags": [
    "a",
    {"x": 1},
    null
  ]
}
'''


def plain(node):
    """The Python value a node stands for (the reader keeps lines; the tests compare it with the standard library's)."""
    if node.kind == "object":
        return {m.key: plain(m.node) for m in node.members}
    if node.kind == "array":
        return [plain(n) for n in node.items]
    return node.value


class Positions(unittest.TestCase):
    def test_every_key_and_value_has_its_line(self):
        root = parse(DOC)
        self.assertEqual((root.kind, root.line, root.end_line), ("object", 1, 19))
        members = {m.key: m for m in root.members}
        self.assertEqual({k: m.key_line for k, m in members.items()}, {"openapi": 2, "info": 3, "paths": 7, "tags": 14})
        get = root.get("paths").get("/invoices").get("get")
        self.assertEqual((get.line, get.end_line), (9, 11))
        self.assertEqual(root.get("paths").member("/invoices").key_line, 8)
        self.assertEqual(get.member("operationId").key_line, 10)
        self.assertEqual(get.get("operationId").value, "listInvoices")

    def test_arrays_and_scalars(self):
        root = parse(DOC)
        tags = root.get("tags")
        self.assertEqual((tags.kind, tags.line, tags.end_line), ("array", 14, 18))
        self.assertEqual([(n.kind, n.line) for n in tags.items], [("string", 15), ("object", 16), ("null", 17)])
        self.assertEqual(tags.items[1].get("x").value, 1)

    def test_the_value_of_a_node_is_plain_python(self):
        root = parse('{"a": [1, 2.5, -3e2, true, false, null, "x"], "b": {}}')
        self.assertEqual(plain(root), {"a": [1, 2.5, -300.0, True, False, None, "x"], "b": {}})

    def test_it_agrees_with_the_standard_library_on_valid_text(self):
        for text in (DOC, '{"a":[]}', "[1,2,3]", '"s"', "12", "true", "null", '{"\u00e9":"\u00fc","k":"\\u00e9\\ud83d\\ude00\\n"}', '{"": {"": []}}'):
            with self.subTest(text=text[:30]):
                self.assertEqual(plain(parse(text)), json.loads(text))

    def test_crlf_and_a_byte_order_mark_do_not_move_lines(self):
        root = parse("\ufeff{\r\n  \"a\": 1,\r\n  \"b\": {\r\n    \"c\": 2\r\n  }\r\n}\r\n")
        self.assertEqual([(m.key, m.key_line) for m in root.members], [("a", 2), ("b", 3)])
        self.assertEqual(root.get("b").member("c").key_line, 4)

    def test_a_lone_carriage_return_is_not_a_line_break(self):
        root = parse('{"a": 1,\r"b": 2}')
        self.assertEqual([m.key_line for m in root.members], [1, 1])  # git and str.split("\n") do not break on it either

    def test_a_key_after_a_string_with_an_escaped_newline_is_on_the_next_line(self):
        root = parse('{\n  "a": "x\\ny",\n  "b": 1\n}')
        self.assertEqual([m.key_line for m in root.members], [2, 3])

    def test_unicode_line_separators_inside_a_string_are_not_line_breaks(self):
        root = parse('{"a": "x\u2028y",\n"b": 1}')
        self.assertEqual([m.key_line for m in root.members], [1, 2])

    def test_a_long_document_is_read_in_linear_time(self):
        body = ",\n".join(f'  "k{i}": {{"a": [1, 2, {{"b": "c"}}]}}' for i in range(20000))
        root = parse("{\n" + body + "\n}")
        self.assertEqual(root.members[-1].key_line, 20001)


class Refusals(unittest.TestCase):
    def fails(self, text, fragment, line=None):
        with self.assertRaises(JsonError) as ctx:
            parse(text)
        self.assertIn(fragment, str(ctx.exception))
        if line is not None:
            self.assertEqual(ctx.exception.line, line)
        return ctx.exception

    def test_empty_and_blank_text(self):
        self.fails("", "empty")
        self.fails("  \n ", "empty")

    def test_trailing_comma_and_trailing_text(self):
        self.fails('{"a": 1,}', "expected a string", 1)
        self.fails('[1, 2,]', "expected a value", 1)
        self.fails('{"a": 1} x', "after the value", 1)
        self.fails('{"a": 1}\n{"b": 2}', "after the value", 2)

    def test_a_truncated_document(self):
        self.fails('{"a": [1, 2', "end of the text")
        self.fails('{"a": "abc', "unterminated string", 1)
        self.fails('{"a"', "end of the text")

    def test_single_quotes_comments_and_bare_words(self):
        self.fails("{'a': 1}", "expected a string")
        self.fails('{"a": 1 /* c */}', "expected , or }")
        self.fails('{"a": nul}', "expected a value")
        self.fails('{"a": NaN}', "expected a value")
        self.fails('{"a": Infinity}', "expected a value")

    def test_numbers_are_strict(self):
        for bad in ("01", "1.", ".5", "+1", "1e", "--1", "0x10"):
            with self.subTest(number=bad):
                self.fails(f'{{"a": {bad}}}', "")

    def test_bad_escapes_and_control_characters_in_a_string(self):
        self.fails('{"a": "\\x"}', "string")
        self.fails('{"a": "\\u12"}', "string")
        self.fails('{"a": "tab\there"}', "string")

    def test_a_duplicate_key_is_refused_because_the_document_is_ambiguous(self):
        err = self.fails('{\n"a": 1,\n"b": {"x": 1, "x": 2}\n}', "duplicate key", 3)
        self.assertIn("'x'", str(err))

    def test_nesting_deeper_than_the_limit_is_refused_not_a_recursion_error(self):
        deep = "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
        self.fails(deep, "nested deeper")
        ok = "[" * MAX_DEPTH + "]" * MAX_DEPTH
        self.assertEqual(parse(ok).kind, "array")
        self.fails("[" * 100000, "nested deeper")

    def test_not_text(self):
        self.fails(None, "text")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
