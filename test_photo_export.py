"""Behavioral checks for the exported picture, including troublesome sources."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageCms

from photo_export import camera_identity, render_framed_photo


class PhotoExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "source.jpg"
        self.assets = Path(__file__).resolve().parent / "ui" / "assets"
        self.metadata = {"camera": "Panasonic DC-S5", "lens": "LUMIX S 20-60/F3.5-5.6", "focal_length": "50 mm", "aperture": "f/5.3", "shutter": "1/60 s", "iso": "6400"}

    def tearDown(self):
        self.temp.cleanup()

    def test_full_export_keeps_dimensions_and_source_and_adds_only_bottom(self):
        Image.new("RGB", (2400, 1600), (32, 66, 119)).save(self.path, quality=98)
        original = self.path.read_bytes()
        result = render_framed_photo(self.path, self.metadata, self.assets)
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (2400, 1893))
            self.assertEqual(image.mode, "RGB")
            self.assertTrue(all(abs(a-b)<3 for a,b in zip(image.getpixel((1200, 800)), (32, 66, 119))))
            self.assertEqual(image.getpixel((10, 1880)), (255, 255, 255))
            # The border is populated in both independent columns.
            self.assertLess(min(image.crop((80, 1640, 820, 1850)).getextrema()[0]), 80)
            self.assertLess(min(image.crop((900, 1640, 2310, 1850)).getextrema()[0]), 80)
        self.assertEqual(self.path.read_bytes(), original)

    def test_preview_is_smaller_without_changing_geometry(self):
        Image.new("RGB", (3000, 2000), "navy").save(self.path)
        with Image.open(BytesIO(render_framed_photo(self.path, self.metadata, self.assets, preview=True))) as image:
            self.assertEqual(image.size, (1600, 1262))
        with Image.open(BytesIO(render_framed_photo(self.path, self.metadata, self.assets))) as image:
            self.assertEqual(image.size, (3000, 2366))

    def test_orientation_is_applied_once_and_old_exif_is_not_copied(self):
        source = Image.new("RGB", (600, 400), "red")
        source.paste((0, 70, 230), (0, 0, 300, 400))
        exif = Image.Exif(); exif[274] = 6; exif[270] = "private/source/path"
        source.save(self.path, exif=exif)
        with Image.open(BytesIO(render_framed_photo(self.path, self.metadata, self.assets))) as image:
            self.assertEqual(image.size, (400, 649))
            self.assertLess(image.getpixel((200, 100))[0], 5)
            self.assertGreater(image.getpixel((200, 500))[0], 240)
            self.assertFalse(image.getexif())

    def test_transparent_image_and_missing_metadata(self):
        path = self.root / "transparent.png"
        source = Image.new("RGBA", (500, 700), (20, 30, 40, 0))
        source.paste((20, 40, 90, 255), (100, 100, 200, 200))
        source.save(path)
        with Image.open(BytesIO(render_framed_photo(path, {}, self.assets))) as image:
            self.assertEqual(image.size, (500, 761))
            self.assertEqual(image.getpixel((20, 20)), (255, 255, 255))
            self.assertEqual(image.crop((0, 710, 500, 760)).getextrema(), ((255, 255),)*3)
            self.assertLess(image.getpixel((150, 150))[2], 100)

    def test_multiple_frames_use_first_and_invalid_profile_is_safe(self):
        path = self.root / "frames.tif"
        Image.new("RGB", (500, 300), "red").save(path, save_all=True, append_images=[Image.new("RGB", (500, 300), "blue")], icc_profile=b"broken profile")
        original = path.read_bytes()
        with Image.open(BytesIO(render_framed_photo(path, self.metadata, self.assets))) as image:
            self.assertGreater(image.getpixel((250, 150))[0], 240)
            self.assertEqual(image.size, (500, 361))
            self.assertEqual(ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"])).profile.xcolor_space.strip(), "RGB")
        self.assertEqual(path.read_bytes(), original)

    def test_tagged_non_srgb_is_converted_before_export(self):
        path = self.root / "color.tif"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        Image.new("LAB", (500, 300), (160, 135, 147)).save(path, icc_profile=profile.tobytes())
        with Image.open(path) as source:
            expected = ImageCms.profileToProfile(source, profile, ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((250, 150))
        with Image.open(BytesIO(render_framed_photo(path, {}, self.assets))) as image:
            self.assertTrue(all(abs(a-b)<3 for a,b in zip(image.getpixel((250, 150)), expected)))
            self.assertIn("sRGB", ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))))

    def test_long_or_missing_metadata_never_replaces_real_camera(self):
        self.assertEqual(camera_identity("Panasonic DC-S5"), ("LUMIX", "DC-S5", "lumix.png"))
        self.assertEqual(camera_identity("NIKON CORPORATION NIKON Z 6"), ("Nikon", "Z 6", None))
        self.assertEqual(camera_identity("RICOH IMAGING COMPANY, LTD. RICOH GR III"), ("RICOH", "GR III", None))
        self.assertEqual(camera_identity("RICOH IMAGING COMPANY, LTD. PENTAX K-1"), ("PENTAX", "K-1", None))
        self.assertEqual(camera_identity(""), ("", "", None))
        self.assertEqual(camera_identity("Unlisted Camera Model"), ("", "Unlisted Camera Model", None))
        Image.new("RGB", (800, 1200), "gray").save(self.path)
        metadata = {**self.metadata, "camera": "未知相机" * 100, "lens": "Lens with very long serial details " * 100}
        with Image.open(BytesIO(render_framed_photo(self.path, metadata, self.assets))) as image:
            self.assertEqual(image.size, (800, 1298))
            # Even extreme metadata leaves the outer margins white.
            self.assertEqual(image.crop((0, 1201, 10, 1297)).getextrema(), ((255, 255),)*3)
            self.assertEqual(image.crop((790, 1201, 800, 1297)).getextrema(), ((255, 255),)*3)


if __name__ == "__main__":
    unittest.main()
