import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from vault.catalog import Catalog, DEFAULT_ROOT, ValidationError
from vault.exporter import ExportManager
from vault.recipes import Recipe, plan


@unittest.skipUnless(Path(DEFAULT_ROOT).is_dir(), "本地 VAULT 资料库未连接")
class AssemblyContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(DEFAULT_ROOT)

    def test_inventory_and_supported_families(self):
        counts = self.catalog.public()["counts"]
        self.assertEqual(counts, {"drawer": 400, "sustainer": 225, "mod": 98})
        self.assertEqual(len(self.catalog.files), 723)

    def test_every_exposed_body_variant_resolves(self):
        checked = 0
        for model in self.catalog.models.values():
            if not model["verified"] or (model["kind"] == "drawer" and model["family"] == "XL"):
                continue
            variants = ["standard"] if model["family"] in ("S", "M") or model["kind"] == "drawer" else ["flat", "dividers", "sealed"]
            for height in model["heights"]:
                for variant in variants:
                    cfg = {"kind": model["kind"], "family": model["family"], "depth": model["depth"], "height": height, "variant": variant}
                    result = Recipe(self.catalog).build(cfg)
                    self.assertTrue(result["parts"])
                    for part in result["parts"]:
                        self.assertTrue(self.catalog.resolve(part["id"]).is_file())
                    checked += 1
        self.assertEqual(checked, 135)

    def test_one_stl_pair_is_not_multiplied_by_object_count(self):
        result = plan(self.catalog, {"configs": [{"kind": "drawer", "height": 1, "quantity": 2}]})
        by_name = {p["name"]: p["copies"] for p in result["parts"]}
        self.assertEqual(by_name["Springs.stl"], 2)
        self.assertEqual(by_name["x3 Runner Guides.stl"], 2)
        self.assertEqual(by_name["Runner Stopper.stl"], 4)
        self.assertNotIn("S Rails.stl", by_name)

    def test_multiple_drawers_share_one_shell_and_get_rails(self):
        result = plan(self.catalog, {"configs": [{"height": 4, "drawers": [2, 1, 1], "roof": "none"}]})
        by_name = {p["name"]: p["copies"] for p in result["parts"]}
        self.assertEqual(by_name["S3 Module 4f.stl"], 1)
        self.assertEqual(by_name["S3 Box 1F.stl"], 2)
        self.assertEqual(by_name["S3 Box 2F.stl"], 1)
        self.assertEqual(by_name["S Rails.stl"], 3)
        self.assertFalse(any("Roof" in name for name in by_name))

    def test_incompatible_height_and_unverified_families_fail_closed(self):
        for cfg in [{"height": 2, "drawers": [1]}, {"height": 3}, {"family": "L²"}, {"family": "XL"}]:
            with self.subTest(cfg=cfg), self.assertRaises(ValidationError):
                Recipe(self.catalog).build(cfg)

    def test_case_lock_and_hinge_sets(self):
        for family, locks, top_copies in [("S", 1, 1), ("M", 1, 1), ("L", 2, 1), ("XL", 2, 1)]:
            result = plan(self.catalog, {"configs": [{"kind": "sustainer", "family": family, "labels": False}]})
            by_name = {p["name"]: p["copies"] for p in result["parts"]}
            self.assertEqual(by_name["1 SET OF PINS FOR 1 LOCK.stl"], locks)
            top = f"{family if family in ('S','M') else 'L-XL'} Hinge Top.stl"
            self.assertEqual(by_name[top], top_copies)
            self.assertEqual(sum(q for name, q in by_name.items() if 'Rods.stl' in name), 1)

    def test_split_roof_excludes_whole_and_adds_connector(self):
        result = plan(self.catalog, {"configs": [{"kind": "sustainer", "depth": 6, "split_roof": True}]})
        names = [p["name"] for p in result["parts"]]
        self.assertIn("S6 Roof Part 1.stl", names)
        self.assertIn("S6 Roof Part 2.stl", names)
        self.assertIn("Connector For Split Roof.stl", names)
        self.assertNotIn("S6 Roof Whole.stl", names)
        with self.assertRaises(ValidationError):
            Recipe(self.catalog).build({"kind": "sustainer", "depth": 3, "split_roof": True})

    def test_case_label_versions_are_height_specific(self):
        for height, expected, excluded in [(1, "Label for Sustainers.stl", "Label.stl"),
                                            (2, "Label.stl", "Label for Sustainers.stl")]:
            result = plan(self.catalog, {"configs": [{"kind": "sustainer", "family": "L", "height": height}]})
            by_name = {p["name"]: p["copies"] for p in result["parts"]}
            self.assertEqual(by_name[expected], 1)
            self.assertNotIn(excluded, by_name)

    def test_split_connection_counts_follow_joint_slots(self):
        for family, expected in [("S", 1), ("M", 1), ("L", 2), ("XL", 2)]:
            result = plan(self.catalog, {"configs": [{"kind": "sustainer", "family": family, "depth": 6,
                                                      "split_roof": True, "base": "split"}]})
            counts = {p["name"]: p["copies"] for p in result["parts"]}
            self.assertEqual(counts["Connector For Split Roof.stl"], expected)
            self.assertEqual(counts["Base Connector.stl"], expected)

    def test_no_guessing_for_missing_or_duplicate_candidates(self):
        with self.assertRaises(ValidationError):
            self.catalog.match("does-not-exist.stl")
        with self.assertRaises(ValidationError):
            self.catalog.match("Connector For Split Roof.stl", kind="sustainer")

    def test_option_dependency_contracts(self):
        for cfg in [{"top_handle": True, "roof": "table"}, {"wheels": True}, {"base": "split", "depth": 3},
                    {"height": 1, "interior": "dividers", "divider_height": "82.5"},
                    {"kind": "sustainer", "family": "L", "variant": "flat", "interior": "dividers"}]:
            with self.subTest(cfg=cfg), self.assertRaises(ValidationError):
                Recipe(self.catalog).build(cfg)

    def test_duplicate_configs_merge_file_copies(self):
        once = plan(self.catalog, {"configs": [{}]})
        twice = plan(self.catalog, {"configs": [{}, {}]})
        self.assertEqual(once["file_count"], twice["file_count"])
        self.assertEqual(once["copies"] * 2, twice["copies"])
        self.assertEqual(once["bytes"], twice["bytes"])


class ExportContracts(unittest.TestCase):
    def test_zip_round_trip_integrity_and_unmodified_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            source = root / "01. DRAWERS" / "07. Universal" / "Springs.stl"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"TEST-STL\n" * 1200)
            original = source.read_bytes()
            catalog = Catalog(root)
            payload = {"manual": [{"id": catalog.files[0].id, "copies": 3}, {"id": catalog.files[0].id, "copies": 2}]}
            manager = ExportManager(Path(tmp) / "output")
            job = manager.start(catalog, payload)
            for _ in range(100):
                state = manager.get(job["id"])
                if state["status"] in ("done", "error"):
                    break
                time.sleep(.02)
            self.assertEqual(state["status"], "done", state)
            with zipfile.ZipFile(manager.download_path(job["id"])) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(len(manifest["parts"]), 1)
                part = manifest["parts"][0]
                self.assertEqual(part["copies"], 5)
                self.assertEqual(hashlib.sha256(archive.read(part["archive_path"])).hexdigest(), part["sha256"])
                self.assertTrue(archive.read("打印清单.csv").startswith(b"\xef\xbb\xbf"))
                self.assertIn("五金清单.csv", archive.namelist())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.name, "Springs.stl")
            self.assertEqual(len(manager.history()), 1)

    def test_empty_and_invalid_manual_exports_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            (root / "01. DRAWERS").mkdir(parents=True)
            (root / "01. DRAWERS" / "test.stl").write_bytes(b"test")
            catalog = Catalog(root)
            manager = ExportManager(Path(tmp) / "out")
            for payload in [{}, {"manual": [{"id": "../../secret", "copies": 1}]},
                            {"manual": [{"id": catalog.files[0].id, "copies": -1}]}]:
                with self.subTest(payload=payload), self.assertRaises(ValidationError):
                    manager.start(catalog, payload)
            with self.assertRaises(ValidationError):
                catalog.resolve("../../secret")
            with self.assertRaises(ValidationError):
                manager.download_path("../../secret")

    def test_deleted_or_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "01. DRAWERS" / "test.stl"
            path.parent.mkdir()
            path.write_bytes(b"first")
            catalog = Catalog(root)
            path.write_bytes(b"changed size")
            with self.assertRaises(ValidationError):
                catalog.resolve(catalog.files[0].id)
            path.unlink()
            with self.assertRaises(ValidationError):
                catalog.resolve(catalog.files[0].id)


if __name__ == "__main__":
    unittest.main()
