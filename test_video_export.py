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

from video_export import VideoExportError, _glass_edge, _glass_mask, _rounded_mask, compose_video_slide, encode_slideshow, find_ffmpeg, render_video_preview


class VideoExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = Path(__file__).resolve().parent / 'ui' / 'assets'
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
            # Keep the entire original white edge. The short frosted transition
            # starts outside it and returns to background within a few pixels.
            self.assertTrue(all(channel>245 for channel in preview.getpixel((480,485))))
            self.assertTrue(all(channel>245 for channel in preview.getpixel((480,478))))
            self.assertGreater(max(preview.getpixel((480,487))),max(preview.getpixel((480,494)))+30)
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

    def test_glass_collar_follows_round_corners_and_keeps_its_short_width(self):
        size, border, radius = (236, 156), 8, 16
        with _glass_mask(size, border, radius) as rounded, _glass_mask(size, border) as rectangular:
            width, height = size
            self.assertEqual(rounded.getpixel((width // 2, height // 2)), 255)
            for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (width // 2, 0), (width // 2, height - 1), (0, height // 2), (width - 1, height // 2)):
                self.assertEqual(rounded.getpixel(point), 0, 'the outermost collar boundary is transparent')
            for point in ((border, border), (width - border - 1, border), (border, height - border - 1), (width - border - 1, height - border - 1)):
                self.assertLess(rounded.getpixel(point), 64, 'the discarded print corners cannot leave an opaque square glass patch')
                self.assertEqual(rectangular.getpixel(point), 255, 'this assertion distinguishes rounded glass from the old rectangular mask')
            transition = [rounded.getpixel((width // 2, y)) for y in range(border + 1)]
            self.assertEqual(transition, sorted(transition))
            self.assertTrue(any(0 < value < 255 for value in transition))
            self.assertEqual(transition[-1], 255)
            self.assertEqual(transition, [rectangular.getpixel((width // 2, y)) for y in range(border + 1)], 'round corners do not widen or alter the straight glass transition')
            for transpose in (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_TOP_BOTTOM):
                with rounded.transpose(transpose) as mirrored, ImageChops.difference(rounded, mirrored) as difference:
                    self.assertIsNone(difference.getbbox(), 'the glass collar follows all four corners symmetrically')
        with Image.new('RGB', (220, 140), (80, 120, 150)) as photograph:
            glass, mask = _glass_edge(photograph, border, radius)
            try:
                self.assertEqual(glass.size, size)
                self.assertEqual(mask.size, size, 'rounded glass keeps exactly the original two-sided collar dimensions')
                self.assertLess(mask.getpixel((border, border)), 64, 'glass edge actually uses the rounded mask')
            finally:
                glass.close(); mask.close()

    def test_composed_round_print_preserves_clear_edges_white_strip_and_rectangular_video(self):
        for source, size in ((self.first, (960, 540)), (self.second, (960, 540)),
                             (self.first, (1280, 720)), (self.second, (1920, 1080))):
            with self.subTest(photo=source.name, resolution=size):
                captured = {}

                def capture_edge(print_image, border, radius=0):
                    captured.update(print=print_image.copy(), border=border, radius=radius)
                    return _glass_edge(print_image, border, radius)

                with patch('video_export._glass_edge', side_effect=capture_edge):
                    frame = compose_video_slide(source, self.meta, self.assets, size)
                reference = captured['print']
                try:
                    self.assertEqual(frame.size, size, 'only the photo card is rounded; the video canvas stays rectangular')
                    self.assertEqual(frame.mode, 'RGB')
                    self.assertGreater(captured['radius'], 0)
                    self.assertEqual(captured['border'], max(2, round(12 * size[0] / 1920)), 'rounding does not enlarge the glass collar')
                    width, height = reference.size
                    x, y = (size[0] - width) // 2, (size[1] - height) // 2
                    for px, py in ((width // 2, 0), (0, height // 2), (width - 1, height // 2), (width // 2, height - 1), (width // 2, height // 2)):
                        self.assertEqual(frame.getpixel((x + px, y + py)), reference.getpixel((px, py)), 'print centre and straight edge middles remain exactly sharp and opaque')
                    self.assertTrue(all(channel > 245 for channel in frame.getpixel((x + width // 2, y + height - 1))), 'the central white information strip remains intact')
                    for px, py in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
                        actual, square = frame.getpixel((x + px, y + py)), reference.getpixel((px, py))
                        self.assertGreater(max(abs(a - b) for a, b in zip(actual, square)), 8, 'every original rectangular corner reveals the soft surround rather than an opaque square print')
                finally:
                    frame.close(); reference.close()

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
