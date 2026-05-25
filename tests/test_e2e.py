"""
End-to-end tests: actually invoke `pyhanko sign` against the bundled demo
files and verify a signed output is produced.

Skipped if pyhanko is not on PATH so the test suite stays runnable in minimal
environments.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from signpdf_ui import core, paths


FIXTURES = Path(__file__).parent / "fixtures"
TEST_CERT = FIXTURES / "test_identity.p12"
# The known password for the bundled test certificate (committed in the repo
# alongside this self-signed cert; not used anywhere else).
TEST_CERT_PASSWORD = "KXzolC-test-pw-s9Ckp7oZ"

_IN_CI = os.getenv("CI") == "true"


def _have_pyhanko() -> bool:
    return shutil.which("pyhanko") is not None


def _pyhanko_available_or_fail():
    if _have_pyhanko():
        return True
    if _IN_CI:
        raise AssertionError("pyhanko not on PATH — required in CI")
    return False


@unittest.skipUnless(_pyhanko_available_or_fail(), "pyhanko not on PATH")
class TestSignE2E(unittest.TestCase):
    """Drives the same code path the TUI uses, end to end."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        # Set up a config dir that mimics what `--init` would produce.
        self._config_dir = self._tmp / "config"
        result = core.init_config(target_dir=self._config_dir)
        # sanity check that templates were installed
        self.assertEqual(set(p.name for p in result.written), set(paths.TEMPLATE_FILENAMES))
        self._cfg = core.load_ui_config(self._config_dir / "signpdf-ui.yml")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_sign(self, input_pdf: Path, field: str) -> Path:
        work_input = self._tmp / input_pdf.name
        shutil.copy2(input_pdf, work_input)
        out = core.output_path_for(work_input)
        cmd = core.build_sign_command(
            input_file=work_input.resolve(),
            output_file=out.resolve(),
            field=field,
            cert_path=TEST_CERT.resolve(),
            pyhanko_config=self._cfg.pyhanko_config,
            style_name=self._cfg.style_name,
        )
        # pyhanko reads the passphrase from stdin when not on a TTY.
        proc = core.run_sign_command(
            cmd,
            pyhanko_config=self._cfg.pyhanko_config,
            stdin=TEST_CERT_PASSWORD + "\n",
            capture_output=True,
            start_new_session=True,
        )
        if proc.returncode != 0:
            self.fail(
                f"pyhanko failed (exit {proc.returncode}).\n"
                f"cmd: {cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return out

    def test_010_sign_existing_field(self):
        out = self._run_sign(
            FIXTURES / "demo-form-with-sign-fields.pdf",
            field="Person3",
        )
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 0)

    def test_020_sign_by_geometry(self):
        out = self._run_sign(
            FIXTURES / "demo-form-raw.pdf",
            field="1/189,578,356,615/X1",
        )
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 0)

    def test_030_wrong_password_not_file_not_found(self):
        """Wrong password must yield a password error, not 'file does not exist'.

        Reproduces the bug where the TUI passed relative paths to
        build_sign_command; when run_sign_command switched cwd to the config
        directory, pyhanko could not find the PDF and reported a misleading
        'INFILE does not exist' error instead of a password error.
        """
        input_pdf = FIXTURES / "demo-form-with-sign-fields.pdf"
        work_input = self._tmp / input_pdf.name
        shutil.copy2(input_pdf, work_input)
        out = core.output_path_for(work_input)

        # Simulate the TUI: build the command with relative paths while cwd
        # is self._tmp, then run it (which switches cwd to the config dir).
        old_cwd = Path.cwd()
        try:
            os.chdir(self._tmp)
            cmd = core.build_sign_command(
                input_file=Path(work_input.name),   # relative path
                output_file=Path(out.name),          # relative path
                field="Person3",
                cert_path=TEST_CERT,
                pyhanko_config=self._cfg.pyhanko_config,
                style_name=self._cfg.style_name,
            )
        finally:
            os.chdir(old_cwd)

        proc = core.run_sign_command(
            cmd,
            pyhanko_config=self._cfg.pyhanko_config,
            stdin="wrong-password\n",
            capture_output=True,
            start_new_session=True,
        )
        self.assertNotEqual(proc.returncode, 0, "expected non-zero exit for wrong password")
        combined = proc.stdout + proc.stderr
        self.assertNotIn(
            "does not exist",
            combined,
            f"'file not found' error suggests paths are not absolute.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertTrue(
            core.is_wrong_password_error(proc.stderr),
            f"expected a password error.\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )


@unittest.skipUnless(_pyhanko_available_or_fail(), "pyhanko not on PATH")
class TestListFieldsE2E(unittest.TestCase):
    def test_010_lists_prepared_fields(self):
        fields = core.list_fields(FIXTURES / "demo-form-with-sign-fields.pdf")
        # The README documents these three field names.
        for expected in ("Person1", "Person2", "Person3"):
            self.assertIn(
                expected,
                fields,
                f"expected {expected!r} in field list, got {fields!r}",
            )


if __name__ == "__main__":
    unittest.main()
