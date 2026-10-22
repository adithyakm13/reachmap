"""The unified diff parser: files, renames, hunks and line numbers."""

import unittest

from reachmap.diffparse import (
    ADDED, COPIED, DELETED, MODIFIED, RENAMED, TYPE_CHANGED, ChangeSet, DiffParseError, parse, unquote,
)


class ParseTests(unittest.TestCase):
    def test_modified_file_numbers_removed_and_added_lines_from_the_hunk_headers(self):
        diff = (
            "diff --git a/src/a.ts b/src/a.ts\n"
            "index 1111111..2222222 100644\n"
            "--- a/src/a.ts\n"
            "+++ b/src/a.ts\n"
            "@@ -3,2 +3,3 @@ function f() {\n"
            "-const x = 1;\n"
            "-const y = 2;\n"
            "+const x = 10;\n"
            "+const y = 20;\n"
            "+const z = 30;\n"
            "@@ -20 +21,0 @@\n"
            "-gone();\n"
        )
        (change,) = parse(diff)
        self.assertEqual((change.path, change.old_path, change.status), ("src/a.ts", None, MODIFIED))
        self.assertTrue(change.content_available)
        first, second = change.hunks
        self.assertEqual([(l.number, l.text) for l in first.removed], [(3, "const x = 1;"), (4, "const y = 2;")])
        self.assertEqual([(l.number, l.text) for l in first.added], [(3, "const x = 10;"), (4, "const y = 20;"), (5, "const z = 30;")])
        self.assertEqual([(l.number, l.text) for l in second.removed], [(20, "gone();")])
        self.assertEqual(second.added, ())
        self.assertEqual((second.old_start, second.old_count, second.new_start, second.new_count), (20, 1, 21, 0))

    def test_pure_addition_has_a_zero_old_count(self):
        (change,) = parse("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -5,0 +6,2 @@\n+one\n+two\n")
        (hunk,) = change.hunks
        self.assertEqual((hunk.old_start, hunk.old_count), (5, 0))
        self.assertEqual([(l.number, l.text) for l in hunk.added], [(6, "one"), (7, "two")])

    def test_a_removed_line_that_looks_like_a_header_is_still_a_removed_line(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n--- a/not-a-header\n-bye\n+++ b/also-not\n+hi\n"
        (change,) = parse(diff)
        self.assertEqual([l.text for l in change.hunks[0].removed], ["-- a/not-a-header", "bye"])
        self.assertEqual([l.text for l in change.hunks[0].added], ["++ b/also-not", "hi"])
        self.assertEqual(change.path, "f")

    def test_rename_with_edits(self):
        diff = (
            "diff --git a/old/name.py b/new/name.py\n"
            "similarity index 80%\n"
            "rename from old/name.py\n"
            "rename to new/name.py\n"
            "index 1..2 100644\n"
            "--- a/old/name.py\n"
            "+++ b/new/name.py\n"
            "@@ -2 +2 @@\n"
            "-a = 1\n"
            "+a = 2\n"
        )
        (change,) = parse(diff)
        self.assertEqual((change.status, change.path, change.old_path, change.similarity), (RENAMED, "new/name.py", "old/name.py", 80))
        self.assertEqual(change.removed_lines[0].number, 2)

    def test_pure_rename_has_no_hunks_and_content_is_available(self):
        diff = "diff --git a/a.txt b/b.txt\nsimilarity index 100%\nrename from a.txt\nrename to b.txt\n"
        (change,) = parse(diff)
        self.assertEqual((change.status, change.path, change.old_path, change.hunks), (RENAMED, "b.txt", "a.txt", ()))
        self.assertFalse(change.binary)
        self.assertTrue(change.content_available)

    def test_added_deleted_and_binary(self):
        diff = (
            "diff --git a/new.txt b/new.txt\nnew file mode 100644\nindex 0000000..1111111\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+hi\n"
            "diff --git a/old.txt b/old.txt\ndeleted file mode 100644\nindex 1111111..0000000\n--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
            "diff --git a/img.png b/img.png\nindex 1..2 100644\nBinary files a/img.png and b/img.png differ\n"
            "diff --git a/blob.bin b/blob.bin\nnew file mode 100644\nindex 0000000..1111111\nBinary files /dev/null and b/blob.bin differ\n"
        )
        added, deleted, png, blob = parse(diff)
        self.assertEqual((added.status, added.path), (ADDED, "new.txt"))
        self.assertEqual((deleted.status, deleted.path), (DELETED, "old.txt"))
        self.assertEqual(deleted.hunks[0].removed[0].number, 1)
        self.assertTrue(png.binary and not png.content_available and png.hunks == () and png.status == MODIFIED)
        self.assertTrue(blob.binary and blob.status == ADDED and blob.path == "blob.bin")

    def test_mode_only_change_keeps_a_name_with_spaces(self):
        (change,) = parse("diff --git a/my file.sh b/my file.sh\nold mode 100644\nnew mode 100755\n")
        self.assertEqual((change.path, change.status, change.hunks), ("my file.sh", MODIFIED, ()))

    def test_quoted_names(self):
        self.assertEqual(unquote('"a\\tb"'), "a\tb")
        self.assertEqual(unquote('"q\\"x"'), 'q"x')
        self.assertEqual(unquote('"caf\\303\\251"'), "caf\u00e9")
        self.assertEqual(unquote("plain"), "plain")
        diff = 'diff --git "a/tab\\there.txt" "b/tab\\there.txt"\nnew file mode 100644\n--- /dev/null\n+++ "b/tab\\there.txt"\n@@ -0,0 +1 @@\n+x\n'
        (change,) = parse(diff)
        self.assertEqual(change.path, "tab\there.txt")

    def test_no_newline_marker_and_crlf(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\r\n\\ No newline at end of file\n+new\r\n\\ No newline at end of file\n"
        (change,) = parse(diff)
        self.assertEqual(change.removed_lines[0].text, "old")
        self.assertEqual(change.added_lines[0].text, "new")

    def test_context_lines_are_numbered_on_both_sides(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -10,3 +10,4 @@\n keep\n-drop\n+add1\n+add2\n tail\n"
        (change,) = parse(diff)
        (hunk,) = change.hunks
        self.assertEqual([(c.old, c.new, c.text) for c in hunk.context], [(10, 10, "keep"), (12, 13, "tail")])
        self.assertEqual(hunk.removed[0].number, 11)
        self.assertEqual([l.number for l in hunk.added], [11, 12])

    def test_type_change_is_one_file(self):
        diff = (
            "diff --git a/link b/link\ndeleted file mode 100644\nindex 1..0\n--- a/link\n+++ /dev/null\n@@ -1 +0,0 @@\n-text\n"
            "diff --git a/link b/link\nnew file mode 120000\nindex 0..2\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+target\n"
            "\\ No newline at end of file\n"
        )
        (change,) = parse(diff)
        self.assertEqual((change.status, change.path), (TYPE_CHANGED, "link"))
        self.assertEqual(len(change.hunks), 2)

    def test_broken_hunk_raises(self):
        with self.assertRaises(DiffParseError):
            parse("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n-only one line\n")
        with self.assertRaises(DiffParseError):
            parse("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ nonsense @@\n")

    def test_empty_diff_has_no_files(self):
        self.assertEqual(parse(b""), ())
        self.assertEqual(parse(""), ())

    def test_bytes_input_with_invalid_utf8_is_decoded_with_replacement(self):
        diff = b"diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-caf\xe9\n+ok\n"
        (change,) = parse(diff)
        self.assertEqual(change.removed_lines[0].text, "caf\ufffd")

    def test_a_quoted_name_ending_in_a_backslash(self):
        for header_name in ('"end\\\\"',):  # git writes the name end\ as "end\\"
            diff = f"diff --git \"a/{header_name[1:-1]}\" \"b/{header_name[1:-1]}\"\nold mode 100644\nnew mode 100755\n"
            (change,) = parse(diff)
            self.assertEqual(change.path, "end\\")
        diff = 'diff --git "a/x\\\\" "b/x\\\\"\nnew file mode 100644\nindex 0000000..e69de29\n'
        (change,) = parse(diff)
        self.assertEqual((change.path, change.status), ("x\\", ADDED))

    def test_a_quoted_name_with_an_escaped_quote_and_a_space(self):
        diff = 'diff --git "a/q\\"x y.txt" "b/q\\"x y.txt"\nnew file mode 100644\nindex 0000000..e69de29\n'
        (change,) = parse(diff)
        self.assertEqual(change.path, 'q"x y.txt')

    def test_copy_is_a_copy(self):
        (change,) = parse("diff --git a/src.txt b/dst.txt\nsimilarity index 100%\ncopy from src.txt\ncopy to dst.txt\n")
        self.assertEqual((change.status, change.path, change.old_path, change.similarity), (COPIED, "dst.txt", "src.txt", 100))

    def test_names_that_are_not_utf8_keep_their_bytes(self):
        self.assertEqual(unquote('"caf\\351"'), "caf\udce9")
        (change,) = parse('diff --git "a/caf\\351.txt" "b/caf\\351.txt"\nnew file mode 100644\nindex 0000000..e69de29\n')
        self.assertFalse(change.path_is_text)
        self.assertEqual(change.path.encode("utf-8", "surrogateescape"), b"caf\xe9.txt")
        (plain,) = parse("diff --git a/ok.txt b/ok.txt\nnew file mode 100644\nindex 0000000..e69de29\n")
        self.assertTrue(plain.path_is_text)

    def test_a_removal_with_a_zero_new_count_numbers_the_old_lines(self):
        (change,) = parse("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -3,2 +2,0 @@\n-x\n-y\n")
        (hunk,) = change.hunks
        self.assertEqual([(l.number, l.text) for l in hunk.removed], [(3, "x"), (4, "y")])
        self.assertEqual((hunk.new_start, hunk.new_count, hunk.added), (2, 0, ()))

    def test_text_that_looks_like_a_marker_or_a_header_inside_a_hunk_is_text(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -0,0 +1,3 @@\n+\n+--- a/evil\n+\\ No newline at end of file\n"
        (change,) = parse(diff)
        self.assertEqual([l.text for l in change.added_lines], ["", "--- a/evil", "\\ No newline at end of file"])

    def test_changeset_counts_files(self):
        files = parse("diff --git a/a b/a\nnew file mode 100644\n--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+x\n")
        cs = ChangeSet(base_sha="a" * 40, head_sha="b" * 40, files=files)
        self.assertEqual((cs.file_count, cs.paths, cs.kind, cs.pre_change_sha), (1, ("a",), "diff", None))


if __name__ == "__main__":
    unittest.main()
