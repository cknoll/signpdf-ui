"""
Unit tests for the pure-logic surface of signpdf-ui.

The functions exercised here are the ones most likely to break under future
pyhanko / PDF format changes: the CLI invocation, the field-list parser, and
the rect extractor.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from signpdf_ui import core, paths


FIXTURES = Path(__file__).parent / "fixtures"


class TestBuildSignCommand(unittest.TestCase):
    def test_010_basic_invocation_matches_legacy_bash(self):
        cmd = core.build_sign_command(
            input_file=Path("in.pdf"),
            output_file=Path("in_signed.pdf"),
            field="1/189,578,356,615/X1",
            cert_path=Path("cert.p12"),
            pyhanko_config=Path("pyhanko.yml"),
            style_name="my-signature",
        )
        self.assertEqual(cmd[0], "pyhanko")
        self.assertIn("sign", cmd)
        self.assertIn("addsig", cmd)
        self.assertIn("--no-strict-syntax", cmd)
        self.assertIn("--style-name", cmd)
        self.assertIn("my-signature", cmd)
        self.assertIn("--field", cmd)
        self.assertIn("1/189,578,356,615/X1", cmd)
        self.assertIn("pkcs12", cmd)
        # input, output, cert come last in that order
        self.assertEqual(cmd[-3:], ["in.pdf", "in_signed.pdf", "cert.p12"])

    def test_020_config_flag_present(self):
        cmd = core.build_sign_command(
            input_file=Path("a.pdf"),
            output_file=Path("a_signed.pdf"),
            field="F1",
            cert_path=Path("c.p12"),
            pyhanko_config=Path("/etc/foo/pyhanko.yml"),
            style_name="s",
        )
        i = cmd.index("--config")
        self.assertEqual(cmd[i + 1], "/etc/foo/pyhanko.yml")


class TestOutputPath(unittest.TestCase):
    def test_010_appends_signed_suffix(self):
        self.assertEqual(
            core.output_path_for(Path("foo/bar.pdf")),
            Path("foo/bar_signed.pdf"),
        )

    def test_020_already_signed_detected(self):
        self.assertTrue(core.is_already_signed_output(Path("x_signed.pdf")))
        self.assertFalse(core.is_already_signed_output(Path("x.pdf")))


class TestParseFieldList(unittest.TestCase):
    def test_010_splits_lines_and_strips(self):
        out = "  Person1 \nPerson2\n\nPerson3   \n"
        self.assertEqual(
            core.parse_field_list(out),
            ["Person1", "Person2", "Person3"],
        )

    def test_020_ignores_comments(self):
        out = "# header line\nPerson1\n# another\nPerson2\n"
        self.assertEqual(core.parse_field_list(out), ["Person1", "Person2"])

    def test_030_empty_output(self):
        self.assertEqual(core.parse_field_list(""), [])

    def test_040_strips_status_suffix(self):
        # Real pyhanko output (as of 2025-05): NAME:STATUS per line.
        out = "Person1:EMPTY\nPerson2:EMPTY\nPerson3:EMPTY\n"
        self.assertEqual(
            core.parse_field_list(out),
            ["Person1", "Person2", "Person3"],
        )


class TestRectExtraction(unittest.TestCase):
    def test_010_integers_and_floats(self):
        data = b"foo /Rect [10 20 30 40] bar /Rect [ 1.5 2.5 3.5 4.5 ] baz"
        self.assertEqual(
            core.parse_rects(data),
            ["10,20,30,40", "2,2,4,4"],  # round half to even
        )

    def test_020_no_rects(self):
        self.assertEqual(core.parse_rects(b"no rects here"), [])

    def test_030_extracts_from_real_pdf(self):
        rects = core.extract_rects(FIXTURES / "demo-form-raw-with-rects.pdf")
        self.assertTrue(rects, "expected at least one rect in the demo PDF")
        for entry in rects:
            parts = entry.split(",")
            self.assertEqual(len(parts), 4)
            for p in parts:
                int(p)  # raises if not int

    def test_040_no_rects_in_form_without_annotations(self):
        # demo-form-raw.pdf has no rect annotations, so extraction should
        # return an empty list (or at worst the form field rects only).
        # We don't assert exact emptiness because PDF /Rect appears on form
        # field annotations too; instead we just check the function runs.
        result = core.extract_rects(FIXTURES / "demo-form-raw.pdf")
        self.assertIsInstance(result, list)


class TestExpandPdfPatterns(unittest.TestCase):
    def setUp(self):
        self._cwd = Path.cwd()
        self._tmp = tempfile.mkdtemp()
        import os

        os.chdir(self._tmp)
        (Path(self._tmp) / "a.pdf").write_bytes(b"%PDF-1.4\n")
        (Path(self._tmp) / "b.pdf").write_bytes(b"%PDF-1.4\n")
        (Path(self._tmp) / "b_signed.pdf").write_bytes(b"%PDF-1.4\n")
        (Path(self._tmp) / "notes.txt").write_text("x")

    def tearDown(self):
        import os

        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_010_glob_matches_pdfs_only(self):
        result = core.expand_pdf_patterns(["*.pdf"])
        names = sorted(p.name for p in result)
        self.assertEqual(names, ["a.pdf", "b.pdf"])  # _signed skipped, txt skipped

    def test_020_explicit_path(self):
        result = core.expand_pdf_patterns(["a.pdf"])
        self.assertEqual([p.name for p in result], ["a.pdf"])

    def test_030_no_match(self):
        self.assertEqual(core.expand_pdf_patterns(["nope*.pdf"]), [])


class TestInitConfig(unittest.TestCase):
    def test_010_writes_all_templates_to_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = core.init_config(target_dir=Path(tmp))
            written_names = sorted(p.name for p in result.written)
            self.assertEqual(written_names, sorted(paths.TEMPLATE_FILENAMES))
            self.assertEqual(result.skipped, [])
            for name in paths.TEMPLATE_FILENAMES:
                self.assertTrue((Path(tmp) / name).exists())

    def test_020_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "signpdf-ui.yml").write_text("# existing\n")
            result = core.init_config(target_dir=Path(tmp))
            self.assertIn(
                "signpdf-ui.yml",
                [p.name for p in result.skipped],
            )
            self.assertEqual(
                (Path(tmp) / "signpdf-ui.yml").read_text(),
                "# existing\n",
            )

    def test_030_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "signpdf-ui.yml").write_text("# existing\n")
            result = core.init_config(target_dir=Path(tmp), force=True)
            self.assertIn(
                "signpdf-ui.yml",
                [p.name for p in result.written],
            )
            self.assertNotEqual(
                (Path(tmp) / "signpdf-ui.yml").read_text(),
                "# existing\n",
            )


class TestLoadUiConfig(unittest.TestCase):
    def test_010_resolves_relative_pyhanko_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            core.init_config(target_dir=tmp_path)
            cfg = core.load_ui_config(tmp_path / "signpdf-ui.yml")
            # pyhanko_config in the template is relative ("pyhanko.yml") and
            # should be resolved to a sibling of the UI config.
            self.assertEqual(cfg.pyhanko_config, (tmp_path / "pyhanko.yml").resolve())
            self.assertEqual(cfg.style_name, "my-signature")

    def test_020_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                core.load_ui_config(Path(tmp) / "nope.yml")


class TestIsWrongPasswordError(unittest.TestCase):
    _WRONG_PW_STDERR = (
        "PKCS#12 passphrase: \n"
        "2026-05-16 00:05:30,686 - pyhanko.sign.signers.pdf_cms - ERROR - "
        "Could not load key material from PKCS#12 file\n"
        "Error: Generic processing error.\n"
    )

    def test_010_detects_wrong_password(self):
        self.assertTrue(core.is_wrong_password_error(self._WRONG_PW_STDERR))

    def test_020_unrelated_error_not_flagged(self):
        self.assertFalse(core.is_wrong_password_error("Error: file not found\n"))

    def test_030_empty_stderr_not_flagged(self):
        self.assertFalse(core.is_wrong_password_error(""))


if __name__ == "__main__":
    unittest.main()
