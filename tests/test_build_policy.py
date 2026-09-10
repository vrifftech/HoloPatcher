"""Onefile/no-UPX regression tests; stdlib only, no compiler or display required."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import build_policy
import build_distributions


class BuildPolicyTests(unittest.TestCase):
    def test_default_is_onefile(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            build_policy.require_onefile()

    def test_onefile_is_accepted(self):
        with mock.patch.dict(os.environ, {"FRONTEND_BUILD_MODE": "OneFile"}):
            build_policy.require_onefile()

    def test_onedir_is_rejected(self):
        with mock.patch.dict(os.environ, {"FRONTEND_BUILD_MODE": "onedir"}):
            with self.assertRaisesRegex(RuntimeError, "Only onefile"):
                build_policy.require_onefile()

    def test_missing_manifest_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MANIFEST is required"):
                build_policy.verify_pyinstaller(Path("unused"), "Linux-64bit-intel", "6.22.2")

    def test_complete_native_manifest_and_tampering(self):
        for system, native, names in (
            ("Linux", "Linux-64bit-intel", ("run", "run_d")),
            ("Windows", "Windows-64bit-intel", ("run.exe", "run_d.exe", "runw.exe", "runw_d.exe")),
            ("Darwin", "Darwin-64bit", ("run", "run_d", "runw", "runw_d")),
        ):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "PyInstaller"
                binary_dir = package / "bootloader" / native
                binary_dir.mkdir(parents=True)
                hashes = {}
                for name in names:
                    content = (system + name).encode()
                    (binary_dir / name).write_bytes(content)
                    hashes[name] = hashlib.sha256(content).hexdigest()
                data = {"schema_version": 1, "system": system, "pyinstaller_platform": native,
                        "pyinstaller_version": "6.22.2", "bootloaders_sha256": hashes}
                manifest = Path(temporary) / "manifest.json"
                manifest.write_text(json.dumps(data), encoding="utf-8")
                with mock.patch.dict(os.environ, {"FRESH_PYINSTALLER_MANIFEST": str(manifest)}), \
                     mock.patch.object(build_policy.platform, "system", return_value=system):
                    self.assertEqual(build_policy.verify_pyinstaller(package, native, "6.22.2"), data)
                    with self.assertRaisesRegex(RuntimeError, "platform/version"):
                        build_policy.verify_pyinstaller(package, native, "6.0")
                    (binary_dir / names[0]).write_bytes(b"replaced")
                    with self.assertRaisesRegex(RuntimeError, "replaced"):
                        build_policy.verify_pyinstaller(package, native, "6.22.2")
                    data["bootloaders_sha256"] = {}
                    manifest.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "exactly all"):
                        build_policy.verify_pyinstaller(package, native, "6.22.2")

    def test_spec_builds_only_onefile_with_upx_off_on_every_os(self):
        with tempfile.TemporaryDirectory() as temporary:
            backend = Path(temporary)
            for library, namespace in (("PyKotor", "pykotor"), ("Utility", "utility")):
                (backend / "Libraries" / library / "src" / namespace).mkdir(parents=True)
            hooks = backend / "Libraries/PyKotor/src/pykotor/__pyinstaller"
            hooks.mkdir()
            (hooks / "hook-pykotor.resource.formats.ncs.compiler.py").write_text("", encoding="utf-8")
            fake_pyi = types.ModuleType("PyInstaller")
            fake_pyi.__file__ = str(backend / "PyInstaller/__init__.py")
            fake_pyi.__version__ = "6.22.2"
            fake_pyi.PLATFORM = "native-test-platform"
            bootstrap = types.ModuleType("holopatcher.bootstrap")
            bootstrap.bootstrap_backend = mock.Mock()
            analysis = types.SimpleNamespace(pure=["pure"], scripts=["script"], binaries=["native-binary"], datas=["data"])
            for target in ("win32", "linux", "darwin"):
                with self.subTest(target=target), \
                     mock.patch.dict(os.environ, {"HOLOPATCHER_PYKOTOR_ROOT": str(backend), "FRONTEND_BUILD_MODE": "onefile"}), \
                     mock.patch.dict(sys.modules, {"PyInstaller": fake_pyi, "holopatcher.bootstrap": bootstrap}), \
                     mock.patch.object(sys, "platform", target), mock.patch.object(sys, "path", sys.path.copy()), \
                     mock.patch.object(build_policy, "verify_pyinstaller") as verify:
                    exe, bundle = mock.Mock(), mock.Mock()
                    collect = mock.Mock(side_effect=AssertionError("No COLLECT/onedir builds allowed"))
                    ns = {"SPEC": str(ROOT / "packaging/HoloPatcher.spec"),
                          "Analysis": mock.Mock(return_value=analysis), "PYZ": mock.Mock(return_value="pyz"),
                          "EXE": exe, "COLLECT": collect, "BUNDLE": bundle}
                    runpy.run_path(str(ROOT / "packaging/HoloPatcher.spec"), init_globals=ns)
                    verify.assert_called_once()
                    exe.assert_called_once()
                    args, kwargs = exe.call_args
                    self.assertIn(analysis.binaries, args)
                    self.assertIn(analysis.datas, args)
                    self.assertIs(kwargs["exclude_binaries"], False)
                    self.assertIs(kwargs["append_pkg"], True)
                    self.assertIs(kwargs["upx"], False)
                    collect.assert_not_called()
                    if target == "darwin":
                        self.assertIs(bundle.call_args.args[0], exe.return_value)
                        self.assertIs(bundle.call_args.kwargs["info_plist"]["LSBackgroundOnly"], False)
                    else:
                        bundle.assert_not_called()

    def test_freeze_uses_spec_policy_not_forbidden_makespec_flags(self):
        with mock.patch.dict(os.environ, {"FRONTEND_BUILD_MODE": "onefile"}), \
             mock.patch.object(build_distributions, "run") as run:
            result = build_distributions.freeze()
            args, kwargs = run.call_args
            command = args[0]
            self.assertTrue(command[-1].endswith("HoloPatcher.spec"))
            self.assertEqual(kwargs["env"]["FRONTEND_BUILD_MODE"], "onefile")
            self.assertNotIn("--onefile", command)
            self.assertNotIn("--noupx", command)
            self.assertEqual(result.name, "onefile")

    def test_embedded_payload_rejects_onedir_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "program"
            binary.write_bytes(b"test fixture, not executable")
            readers = types.ModuleType("PyInstaller.archive.readers")
            readers.CArchiveReader = mock.Mock()
            with mock.patch.dict(sys.modules, {"PyInstaller.archive.readers": readers}):
                readers.CArchiveReader.return_value.toc = {"PYZ.pyz": (0, 1, 1, 0, "z")}
                with self.assertRaisesRegex(RuntimeError, "not the required onefile"):
                    build_policy.verify_onefile_payload(binary)
                readers.CArchiveReader.return_value.toc["libpython.so"] = (0, 1, 1, 0, "b")
                self.assertEqual(build_policy.verify_onefile_payload(binary)["mode"], "onefile")
                readers.CArchiveReader.return_value.toc["external"] = (0, 1, 1, 0, "d")
                with self.assertRaisesRegex(RuntimeError, "External"):
                    build_policy.verify_onefile_payload(binary)

    def test_appimage_contains_one_executable_not_onedir(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "packaging").mkdir()
            (root / "packaging/appimage-tools.json").write_text(
                json.dumps({key: {"url": "https://github.com/AppImage/test", "sha256": "0" * 64}
                            for key in ("appimagetool", "runtime")}), encoding="utf-8")
            icon = root / "src/holopatcher/resources/icons/patcher_icon_v2.png"
            icon.parent.mkdir(parents=True)
            icon.write_bytes(b"icon fixture")
            payload = root / "payload"
            payload.write_bytes(b"onefile fixture")
            release = root / "release"
            release.mkdir()
            def fake_run(command, **kwargs):
                if "--runtime-file" in command:
                    Path(command[-1]).write_bytes(b"AppImage fixture")
            with mock.patch.object(build_distributions, "ROOT", root), \
                 mock.patch.object(build_distributions, "download_checked"), \
                 mock.patch.object(build_distributions, "verify_onefile_payload"), \
                 mock.patch.object(build_distributions, "run", side_effect=fake_run), \
                 mock.patch.object(build_distributions, "smoke_test") as smoke:
                build_distributions.build_appimage(payload, release, "test", gui=False)
            appdir = root / "dist/HoloPatcher.AppDir"
            self.assertEqual([p.name for p in (appdir / "usr/bin").iterdir()], ["HoloPatcher"])
            self.assertEqual((appdir / "usr/bin/HoloPatcher").read_bytes(), payload.read_bytes())
            launcher = (appdir / "AppRun").read_text(encoding="utf-8")
            self.assertIn('exec "$HERE/usr/bin/HoloPatcher" "$@"', launcher)
            smoke.assert_called_once_with(release / "test.AppImage", gui=False, appimage=True)
            self.assertTrue((release / "test.AppImage.tar.gz").is_file())


if __name__ == "__main__":
    unittest.main()
