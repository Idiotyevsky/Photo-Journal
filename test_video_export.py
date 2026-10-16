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

from PIL import Image, ImageOps

from video_export import VideoExportError, compose_video_slide, encode_slideshow, find_ffmpeg, render_video_preview


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
