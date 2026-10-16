"""Exercise actual image composition, MP4 encoding, audio looping, and cancel."""
import hashlib
from io import BytesIO
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from PIL import Image, ImageChops, ImageOps

from photo_export import render_framed_photo
from video_export import VideoExportError, _background_grain, _rounded_mask, _video_background, compose_video_slide, encode_slideshow, find_ffmpeg, render_video_preview


class VideoExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = Path(__file__).resolve().parents[1] / 'ui' / 'assets'
        self.first, self.second = self.root / 'landscape.jpg', self.root / 'portrait.jpg'
        Image.new('RGB', (1200, 800), (181, 90, 69)).save(self.first, quality=98)
        Image.new('RGB', (800, 1200), (53, 95, 138)).save(self.second, quality=98)
        self.meta = {'camera':'Panasonic DC-S5','lens':'LUMIX S 20-60/F3.5-5.6','focal_length':'50 mm','aperture':'f/5.3','shutter':'1/60 s','iso':'6400'}
        self.options = {'resolution':'720p','seconds_per_photo':3,'volume':.4}

    def tearDown(self):
        self.temp.cleanup()

    def ffmpeg(self, arguments):
        return subprocess.run([str(find_ffmpeg()), '-hide_banner', '-loglevel', 'info', '-nostdin', *arguments], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=20)

    def frame_at(self, path, seconds):
        result = self.ffmpeg(['-ss', str(seconds), '-i', str(path), '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'png', '-'])
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        with Image.open(BytesIO(result.stdout)) as image:
            return image.copy()

    def test_preview_centres_entire_print_and_retains_white_parameters_strip(self):
        before = hashlib.sha256(self.first.read_bytes()).hexdigest()
        with Image.open(BytesIO(render_video_preview(self.first, self.meta, self.assets))) as preview:
            self.assertEqual(preview.size, (960, 540))
            self.assertTrue(all(abs(a-b)<4 for a,b in zip(preview.getpixel((480,270)), (181,90,69))))
            self.assertTrue(all(channel>245 for channel in preview.getpixel((480,450))))
            # The white strip ends cleanly, with no extended bright glass halo.
            self.assertTrue(all(channel>245 for channel in preview.getpixel((480,485))))
            self.assertTrue(all(channel>245 for channel in preview.getpixel((480,478))))
            self.assertLess(max(abs(a-b) for a,b in zip(preview.getpixel((480,487)),preview.getpixel((480,494)))),12)
            self.assertGreater(min(preview.getpixel((480,485))),max(preview.getpixel((480,487)))+50)
            self.assertLess(max(abs(a-b) for a,b in zip(preview.getpixel((480,491)),preview.getpixel((480,494)))),10)
            # The surround is colored and darker than the crisp photo, not an
            # empty black/white pillarbox or a stretched full-screen image.
            outside=preview.getpixel((50,270))
            self.assertTrue(20<outside[0]<170)
            self.assertNotEqual(outside,preview.getpixel((480,270)))
        self.assertEqual(hashlib.sha256(self.first.read_bytes()).hexdigest(), before)
        with compose_video_slide(self.second,self.meta,self.assets,(1280,720)) as portrait:
            self.assertTrue(all(c>245 for c in portrait.getpixel((640,625))))
            self.assertLess(portrait.getpixel((70,360))[2],portrait.getpixel((640,360))[2])

    def test_background_uses_whole_original_without_white_strip_or_extra_crop(self):
        large_landscape = self.root / 'large-landscape.jpg'
        large_portrait = self.root / 'large-portrait.jpg'
        Image.new('RGB', (3000, 2000), (91, 135, 172)).save(large_landscape)
        Image.new('RGB', (2000, 3000), (121, 145, 87)).save(large_portrait)
        original_fit = ImageOps.fit
        # Fixed expected dimensions catch an obsolete white-bar ratio in the
        # video crop, including after the print renderer reduces preview width.
        for source, expected in ((self.first, (1200, 800)),
                                 (self.second, (800, 1200)),
                                 (large_landscape, (1600, 1067)),
                                 (large_portrait, (1600, 2400))):
            with self.subTest(photo=source.name):
                background_sources = []

                def capture_fit(image, size, *args, **kwargs):
                    background_sources.append(image.size)
                    return original_fit(image, size, *args, **kwargs)

                with patch('video_export.ImageOps.fit', side_effect=capture_fit):
                    with compose_video_slide(source, self.meta, self.assets, (960, 540)) as frame:
                        self.assertEqual(frame.size, (960, 540))
                self.assertEqual(background_sources, [expected],
                                 'soft surround must receive the entire photograph, with only its added white strip removed')

    def test_rounded_contour_is_antialiased_symmetric_and_opaque_inside(self):
        for size, radius in (((211, 137), 13), ((137, 211), 17)):
            with self.subTest(size=size):
                with _rounded_mask(size, radius) as mask:
                    width, height = size
                    self.assertEqual(mask.mode, 'L')
                    self.assertEqual(mask.size, size)
                    self.assertEqual(mask.getpixel((width // 2, height // 2)), 255)
                    for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
                        self.assertEqual(mask.getpixel(point), 0, 'all four rectangular corners are cut away')
                    for point in ((width // 2, 0), (0, height // 2), (width - 1, height // 2), (width // 2, height - 1), (radius + 2, radius + 2)):
                        self.assertEqual(mask.getpixel(point), 255, 'only the curved contour softens, never the print interior or straight edges')
                    with mask.crop((0, 0, radius + 2, radius + 2)) as corner:
                        self.assertTrue(any(0 < value < 255 for value in corner.tobytes()), 'curved contour contains genuine antialias alpha values')
                    for transpose in (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_TOP_BOTTOM):
                        with mask.transpose(transpose) as mirrored, ImageChops.difference(mask, mirrored) as difference:
                            self.assertIsNone(difference.getbbox(), 'all four antialiased corners have identical geometry')
        with _rounded_mask((31, 17), 0) as square:
            self.assertEqual(square.getextrema(), (255, 255), 'zero radius preserves the fully opaque rectangular fallback')

    def test_background_is_luminous_with_subtle_neutral_deterministic_texture(self):
        size, color = (640, 360), (120, 150, 180)
        luminance = lambda rgb: .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]
        with Image.new('RGB', (512, 320), color) as photograph:
            before = photograph.tobytes()
            _background_grain.cache_clear()
            first = _video_background(photograph, size)
            # Regenerate the texture rather than merely reusing cached bytes.
            _background_grain.cache_clear()
            repeated = _video_background(photograph, size)
            with patch('video_export._background_grain', return_value=bytes([128]) * (size[0] * size[1])):
                smooth = _video_background(photograph, size)
            try:
                self.assertEqual(first.size, size)
                self.assertEqual(first.mode, 'RGB')
                self.assertEqual(first.tobytes(), repeated.tobytes(), 'freshly generated texture is stable across repeated renders')
                self.assertEqual(photograph.tobytes(), before, 'background effects never mutate the photograph')
                for point in ((0, 0), (639, 0), (0, 359), (639, 359), (320, 180)):
                    light = luminance(first.getpixel(point))
                    self.assertGreater(light, luminance(color) * .84, 'even the corners retain the brighter background light')
                    self.assertLess(light, luminance(color) * 1.08, 'soft light does not wash the photograph colors out')
                with ImageChops.subtract(first, smooth, offset=128) as delta:
                    red, green, blue = delta.split()
                    try:
                        with ImageChops.difference(red, green) as red_green, ImageChops.difference(green, blue) as green_blue:
                            self.assertIsNone(red_green.getbbox(), 'grain is neutral rather than colored speckle')
                            self.assertIsNone(green_blue.getbbox())
                        low, high = red.getextrema()
                        self.assertGreaterEqual(low, 126)
                        self.assertLessEqual(high, 130)
                        self.assertLess(low, 128)
                        self.assertGreater(high, 128, 'a small real texture remains within two tonal levels of the matte background')
                    finally:
                        red.close(); green.close(); blue.close()
            finally:
                first.close(); repeated.close(); smooth.close()

    def test_composed_round_print_preserves_clear_edges_white_strip_and_rectangular_video(self):
        for source, size in ((self.first, (960, 540)), (self.second, (960, 540)),
                             (self.first, (1280, 720)), (self.second, (1920, 1080))):
            with self.subTest(photo=source.name, resolution=size):
                captured = {}

                def capture_background(photograph, target_size):
                    background = _video_background(photograph, target_size)
                    captured['background'] = background.copy()
                    return background

                def capture_mask(print_size, radius):
                    mask = _rounded_mask(print_size, radius)
                    captured.update(mask=mask.copy(), radius=radius)
                    return mask

                with patch('video_export._video_background', side_effect=capture_background), patch('video_export._rounded_mask', side_effect=capture_mask):
                    frame = compose_video_slide(source, self.meta, self.assets, size)
                with Image.open(BytesIO(render_framed_photo(source, self.meta, self.assets, preview=True))) as print_image:
                    reference = print_image.convert('RGB')
                reference.thumbnail((round(size[0] * .78), round(size[1] * .80)), Image.Resampling.LANCZOS)
                mask, background = captured['mask'], captured['background']
                try:
                    self.assertEqual(frame.size, size, 'only the photo card is rounded; the video canvas stays rectangular')
                    self.assertEqual(frame.mode, 'RGB')
                    self.assertGreater(captured['radius'], 0)
                    self.assertEqual(mask.mode, 'L')
                    self.assertEqual(mask.size, reference.size)
                    width, height = reference.size
                    x, y = (size[0] - width) // 2, (size[1] - height) // 2
                    for px, py in ((width // 2, 0), (0, height // 2), (width - 1, height // 2), (width // 2, height - 1), (width // 2, height // 2)):
                        self.assertEqual(frame.getpixel((x + px, y + py)), reference.getpixel((px, py)), 'print centre and straight edge middles remain exactly sharp and opaque')
                    self.assertTrue(all(channel > 245 for channel in frame.getpixel((x + width // 2, y + height - 1))), 'the central white information strip remains intact')
                    # Compare every fully opaque print pixel and every fully
                    # transparent background pixel, allowing only contour AA.
                    with frame.crop((x, y, x + width, y + height)) as print_area, ImageChops.difference(print_area, reference) as difference, mask.point(lambda value: 255 if value == 255 else 0) as opaque, opaque.convert('RGB') as opaque_rgb, ImageChops.multiply(difference, opaque_rgb) as changed:
                        self.assertIsNone(changed.getbbox(), 'all opaque photo/EXIF pixels remain exactly crisp')
                    with Image.new('L', size, 0) as full_mask:
                        full_mask.paste(mask, (x, y))
                        with full_mask.point(lambda value: 255 if value == 0 else 0) as outside, outside.convert('RGB') as outside_rgb, ImageChops.difference(frame, background) as difference, ImageChops.multiply(difference, outside_rgb) as leaked:
                            self.assertIsNone(leaked.getbbox(), 'nothing blurs or glows beyond the rounded print contour')
                    for point in ((x - 1, y + height // 2), (x + width, y + height // 2), (x + width // 2, y - 1), (x + width // 2, y + height)):
                        self.assertEqual(frame.getpixel(point), background.getpixel(point), 'the first exterior pixel is already the unmodified matte background')
                    for px, py in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
                        actual, square = frame.getpixel((x + px, y + py)), reference.getpixel((px, py))
                        self.assertGreater(max(abs(a - b) for a, b in zip(actual, square)), 8, 'every original rectangular corner reveals the soft surround rather than an opaque square print')
                finally:
                    frame.close(); reference.close(); mask.close(); background.close()

    def test_mp4_contains_real_dissolve_and_looped_audio_to_exact_duration(self):
        audio=self.root/'short.wav'
        with wave.open(str(audio),'wb') as wav:
            wav.setparams((1,2,48000,0,'NONE','not compressed'))
            wav.writeframes(b''.join(struct.pack('<h',5000 if i%80<40 else -5000) for i in range(12000)))
        output=self.root/'movie.mp4';progress=[]
        result=encode_slideshow([(self.first,self.meta),(self.second,self.meta)],self.assets,audio,output,self.options,lambda p,m:progress.append(p),threading.Event())
        self.assertEqual(result,{'duration':5.2,'width':1280,'height':720})
        probe=self.ffmpeg(['-i',str(output),'-f','null','-'])
        text=probe.stderr.decode(errors='replace')
        self.assertEqual(probe.returncode,0,text)
        self.assertRegex(text,r'Duration: 00:00:05\.2\d')
        self.assertIn('Video: h264',text);self.assertIn('Audio: aac',text);self.assertIn('48000 Hz',text)
        first=self.frame_at(output,.5);middle=self.frame_at(output,2.6);last=self.frame_at(output,4)
        try:
            a,b,c=(im.getpixel((640,360)) for im in (first,middle,last))
            self.assertGreater(a[0],c[0]+80)
            self.assertGreater(c[2],a[2]+40)
            self.assertTrue(c[0]+15<b[0]<a[0]-15)
            self.assertTrue(a[2]+10<b[2]<c[2]-10)
        finally:
            first.close();middle.close();last.close()
        self.assertEqual(progress,sorted(progress))
        self.assertFalse(list(self.root.glob('render-*')))

    def test_one_photo_without_music_has_full_hold_and_no_audio(self):
        output=self.root/'one.mp4'
        result=encode_slideshow([(self.first,{})],self.assets,None,output,self.options,lambda *_:None,threading.Event())
        self.assertEqual(result['duration'],3)
        text=self.ffmpeg(['-i',str(output),'-f','null','-']).stderr.decode(errors='replace')
        self.assertIn('Duration: 00:00:03.00',text)
        self.assertNotIn('Audio:',text)

    def test_high_resolution_print_keeps_original_fine_detail_and_card_size(self):
        source = self.root / 'fine-detail.png'
        with Image.frombytes('L', (4000, 1), bytes(30 if (x // 3) % 2 == 0 else 230 for x in range(4000))) as row:
            with row.resize((4000, 2000), Image.Resampling.NEAREST) as image:
                image.save(source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        for size, minimum_width in (((2560, 1440), 1900), ((3840, 2160), 2900)):
            with self.subTest(size=size), patch('video_export._rounded_mask', wraps=_rounded_mask) as mask:
                with compose_video_slide(source, self.meta, self.assets, size) as frame:
                    self.assertEqual(frame.size, size)
                    self.assertGreater(mask.call_args.args[0][0], minimum_width,
                                       'the print fills its intended area instead of remaining capped at 1600 px')
                    with frame.crop((size[0] // 2 - 100, size[1] // 2, size[0] // 2 + 100, size[1] // 2 + 1)) as detail:
                        low, high = detail.getextrema()[0]
                        self.assertGreater(high - low, 170, 'fine alternating detail survives original-resolution sampling')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_real_high_resolution_mp4s_default_to_4k_and_support_1440p(self):
        for resolution, size, level in ((None, (3840, 2160), '5.1'), ('1440p', (2560, 1440), '5.0')):
            with self.subTest(resolution=resolution):
                options = {'seconds_per_photo': 3}
                if resolution:
                    options['resolution'] = resolution
                output = self.root / f'{resolution or "default"}.mp4'
                from video_export import _encode
                with patch('video_export._encode', wraps=_encode) as encoder:
                    result = encode_slideshow([(self.first, self.meta), (self.second, self.meta)],
                                              self.assets, None, output, options, lambda *_: None, threading.Event())
                self.assertEqual((result['width'], result['height']), size)
                self.assertEqual(result['duration'], 5.2)
                command = encoder.call_args.args[0]
                self.assertEqual(command[command.index('-level:v') + 1], level)
                self.assertEqual(command[command.index('-crf') + 1], '16')
                with self.frame_at(output, 2.6) as frame:
                    self.assertEqual(frame.size, size, 'the saved MP4 actually decodes at the chosen resolution')
                    red, green, blue = frame.getpixel((size[0] // 2, size[1] // 2))
                    self.assertTrue(80 < red < 155 and 85 < blue < 125, 'the high-resolution dissolve blends both photos')
                probe = self.ffmpeg(['-i', str(output), '-c:v', 'copy', '-f', 'null', '-'])
                self.assertEqual(probe.returncode, 0, probe.stderr.decode(errors='replace'))
                self.assertIn('Video: h264', probe.stderr.decode(errors='replace'))
                self.assertFalse(list(self.root.glob('render-*')))

    def test_cancelling_encoder_removes_partial_output_and_work_frames(self):
        cancelled=threading.Event();output=self.root/'cancel.mp4';started=time.monotonic()
        def progress(value,message):
            if value>=40:cancelled.set()
        with self.assertRaises(VideoExportError):
            encode_slideshow([(self.first,self.meta)],self.assets,None,output,{**self.options,'seconds_per_photo':10},progress,cancelled)
        self.assertLess(time.monotonic()-started,6)
        self.assertFalse(output.exists());self.assertFalse(list(self.root.glob('render-*')))

    def test_invalid_or_empty_export_is_rejected_before_writing_output(self):
        output=self.root/'invalid.mp4'
        for slides,options in (([],self.options), ([(self.first,{})],{**self.options,'volume':float('nan')}), ([(self.first,{})],{**self.options,'seconds_per_photo':True})):
            with self.assertRaises(VideoExportError):
                encode_slideshow(slides,self.assets,None,output,options,lambda *_:None,threading.Event())
        self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
