# Video export components

Video export invokes a separate FFmpeg executable locally; it does not use a
cloud video service or require an encoder installed on the destination PC.

## imageio-ffmpeg 0.6.0

Project: https://github.com/imageio/imageio-ffmpeg
Distribution: https://pypi.org/project/imageio-ffmpeg/0.6.0/
License: BSD-2-Clause; see `imageio-ffmpeg-LICENSE.txt`.

## FFmpeg 7.1 essentials Windows x64

The executable is supplied by the official imageio-ffmpeg Windows wheel and
identifies itself as `7.1-essentials_build-www.gyan.dev`. It is invoked as a
separate process to encode H.264/AAC MP4 files. No browser or network is used
while making a video.

FFmpeg project: https://ffmpeg.org/
Build provider: https://www.gyan.dev/ffmpeg/builds/
Source release: https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz
Build scripts/dependency references: https://github.com/GyanD/codexffmpeg
License for this GPL-enabled build: GPL version 3 or later; see
`FFmpeg-GPLv3.txt`. The license texts and this notice are bundled with the app.
