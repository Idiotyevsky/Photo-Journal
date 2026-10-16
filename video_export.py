"""Local MP4 slideshows with a crisp framed print on a soft glass surround."""
from io import BytesIO
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from photo_export import framed_border_height, render_framed_photo


FPS = 30
TRANSITION = .8
SIZES = {"1080p": (1920, 1080), "720p": (1280, 720)}


class VideoExportError(Exception):
    """A failure that can be shown directly in the album's export dialog."""


def find_ffmpeg() -> Path:
    """Use only the packaged encoder, so exported albums work on a new PC."""
    try:
        import imageio_ffmpeg
        directory = Path(imageio_ffmpeg.__file__).resolve().parent / "binaries"
        candidates = sorted(path for path in directory.glob("ffmpeg*") if path.is_file() and
                            (path.suffix.lower() == ".exe" if os.name == "nt" else os.access(path, os.X_OK)))
        if candidates:
            return candidates[0]
    except (ImportError, OSError):
        pass
    raise VideoExportError("视频组件尚未准备好，请重新打开最新版相册。")


def _glass_mask(size: tuple[int, int], border: int) -> Image.Image:
    """A short outward transition; its fully opaque centre is covered by the print."""
    width, height = size

    def smooth(value):
        t = max(0., min(1., value))
        return round(255 * t * t * (3 - 2 * t))

    across = Image.new("L", (width, 1))
    across.putdata([smooth(min(x, width - 1 - x) / border) for x in range(width)])
    down = Image.new("L", (1, height))
    down.putdata([smooth(min(y, height - 1 - y) / border) for y in range(height)])
    horizontal = across.resize(size)
    vertical = down.resize(size)
    mask = ImageChops.multiply(horizontal, vertical)
    across.close(); down.close(); horizontal.close(); vertical.close()
    return mask


def _glass_edge(print_image: Image.Image, border: int) -> tuple[Image.Image, Image.Image]:
    """Continue only the outer edge pixels into a thin frosted-glass collar."""
    width, height = print_image.size
    expanded = Image.new("RGB", (width + border * 2, height + border * 2))
    expanded.paste(print_image, (border, border))
    strips = (
        ((0, 0, 1, height), (border, height), (0, border)),
        ((width - 1, 0, width, height), (border, height), (width + border, border)),
        ((0, 0, width, 1), (width, border), (border, 0)),
        ((0, height - 1, width, height), (width, border), (border, height + border)),
    )
    for crop, size, position in strips:
        edge = print_image.crop(crop).resize(size)
        expanded.paste(edge, position)
        edge.close()
    draw = ImageDraw.Draw(expanded)
    for point, box in (
        ((0,0),(0,0,border-1,border-1)),
        ((width-1,0),(width+border,0,width+border*2-1,border-1)),
        ((0,height-1),(0,height+border,border-1,height+border*2-1)),
        ((width-1,height-1),(width+border,height+border,width+border*2-1,height+border*2-1)),
    ):
        draw.rectangle(box, fill=print_image.getpixel(point))
    frosted = expanded.filter(ImageFilter.GaussianBlur(max(.6, border * .45)))
    expanded.close()
    frosted = ImageEnhance.Color(frosted).enhance(.75)
    white = Image.new("RGB", frosted.size, (245, 249, 253))
    glass = Image.blend(frosted, white, .12)
    frosted.close(); white.close()
    return glass, _glass_mask(glass.size, border)


def compose_video_slide(original_path: Path, photo_dict: dict, assets_dir: Path, size=(1920, 1080)) -> Image.Image:
    """The same composition is used for the dialog preview and every MP4 frame."""
    width, height = size
    scale = width / 1920
    with Image.open(BytesIO(render_framed_photo(original_path, photo_dict, assets_dir, preview=True))) as source:
        framed = source.convert("RGB")
    # The background uses the photograph alone, without smearing its white bar.
    photograph = framed.crop((0, 0, framed.width, framed.height - framed_border_height(framed.width)))
    small = ImageOps.fit(photograph, (max(1, width // 4), max(1, height // 4)), Image.Resampling.LANCZOS)
    photograph.close()
    soft = small.filter(ImageFilter.GaussianBlur(13 * scale))
    small.close()
    background = soft.resize(size, Image.Resampling.BICUBIC)
    soft.close()
    background = ImageEnhance.Color(background).enhance(.78)
    background = ImageEnhance.Brightness(background).enhance(.81).convert("RGBA")
    # A broad vignette gives the glass surround depth without a hard dark frame.
    vignette = Image.radial_gradient("L").resize(size, Image.Resampling.BICUBIC).point(lambda value: round(value * .21))
    shade = Image.new("RGBA", size, (17, 22, 28, 0))
    shade.putalpha(vignette)
    background = Image.alpha_composite(background, shade)
    shade.close(); vignette.close()

    framed.thumbnail((round(width * .78), round(height * .80)), Image.Resampling.LANCZOS)
    x, y = (width - framed.width) // 2, (height - framed.height) // 2
    # The photograph remains fully opaque right through its original boundary.
    # Only a narrow collar outside that boundary refracts/softens into the backdrop.
    border = max(2, round(12 * scale))
    glass, mask = _glass_edge(framed, border)
    background.paste(glass, (x - border, y - border), mask)
    glass.close(); mask.close()
    background.paste(framed, (x, y))
    framed.close()
    result = background.convert("RGB")
    background.close()
    return result


def render_video_preview(original_path: Path, photo_dict: dict, assets_dir: Path) -> bytes:
    frame = compose_video_slide(original_path, photo_dict, assets_dir, size=(960, 540))
    result = BytesIO()
    frame.save(result, "JPEG", quality=94, subsampling=0)
    frame.close()
    return result.getvalue()


def _check_cancelled(cancelled):
    if cancelled.is_set():
        raise VideoExportError("已取消视频导出。")


def _encode(command, work_dir: Path, duration: float, cancelled, on_progress):
    """Keep cancellation responsive even while the encoder is not printing."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with (work_dir / "encoder.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=work_dir, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=log, creationflags=creation_flags)
        latest = [0.0]

        def read_progress():
            try:
                for line in process.stdout:
                    if line.startswith(b"out_time_us="):
                        try:
                            latest[0] = max(0., float(line.split(b"=", 1)[1]) / 1_000_000)
                        except ValueError:
                            pass
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        last_progress = -1
        deadline = time.monotonic() + max(240, duration * 4)
        try:
            while process.poll() is None:
                _check_cancelled(cancelled)
                if time.monotonic() > deadline:
                    raise VideoExportError("这次制作耗时过长，可以减少照片或选择 720p 后重试。")
                progress = min(99, round(40 + 59 * latest[0] / duration, 1))
                if progress != last_progress:
                    on_progress(progress, "正在生成视频…")
                    last_progress = progress
                cancelled.wait(.15)
            _check_cancelled(cancelled)
            if process.returncode:
                raise VideoExportError("视频未能生成，请检查所选配乐是否可以播放，或更换配乐后重试。")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            reader.join(timeout=2)
            if process.stdout:
                process.stdout.close()


def encode_slideshow(slides: list[tuple[Path, dict]], assets_dir: Path, music_path: Path | None,
                     output_path: Path, options: dict, on_progress, cancelled: threading.Event) -> dict:
    """Stream a timed image sequence into H.264; memory is bounded to two slides."""
    _check_cancelled(cancelled)
    ffmpeg = find_ffmpeg()
    count = len(slides)
    seconds = options.get("seconds_per_photo", 5)
    resolution = options.get("resolution", "1080p")
    volume = options.get("volume", .5)
    if (not 1 <= count <= 300 or resolution not in SIZES or type(seconds) not in (int, float) or
            seconds not in (3, 4, 5, 6, 8, 10) or isinstance(volume, bool) or
            not isinstance(volume, (int, float)) or not math.isfinite(volume) or not 0 <= volume <= 1):
        raise VideoExportError("视频选项不正确，请重新选择。")
    duration = count * seconds - (count - 1) * TRANSITION
    if duration > 1800:
        raise VideoExportError("每次视频最长 30 分钟，可以分成多个作品集导出。")
    width, height = SIZES[resolution]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    transitions = round(TRANSITION * FPS)
    expected_files = count + (count - 1) * transitions
    needed = 256 * 1024 ** 2 + expected_files * width * height * .18
    if shutil.disk_usage(output_path.parent).free < needed:
        raise VideoExportError("剩余磁盘空间不足，请清理一些空间后再导出。")

    with tempfile.TemporaryDirectory(prefix="render-", dir=output_path.parent) as temporary:
        work = Path(temporary)
        for index, (path, photo) in enumerate(slides):
            _check_cancelled(cancelled)
            on_progress(18 * index / count, f"正在制作画面 {index + 1} / {count}…")
            frame = compose_video_slide(path, photo, assets_dir, (width, height))
            try:
                frame.save(work / f"slide-{index:04d}.jpg", "JPEG", quality=95, subsampling=0)
            finally:
                frame.close()

        entries = ["ffconcat version 1.0"]

        def add_frame(name, length):
            # Every name is generated above, never derived from an imported path.
            entries.extend((f"file '{name}'", f"option framerate {FPS}", f"duration {length:.9f}"))

        for index in range(count):
            _check_cancelled(cancelled)
            hold = seconds - TRANSITION * ((index > 0) + (index < count - 1))
            add_frame(f"slide-{index:04d}.jpg", hold)
            if index == count - 1:
                continue
            with Image.open(work / f"slide-{index:04d}.jpg") as first, Image.open(work / f"slide-{index+1:04d}.jpg") as second:
                for step in range(1, transitions + 1):
                    _check_cancelled(cancelled)
                    t = step / (transitions + 1)
                    alpha = t * t * (3 - 2 * t)
                    blended = Image.blend(first, second, alpha)
                    name = f"fade-{index:04d}-{step:02d}.jpg"
                    try:
                        blended.save(work / name, "JPEG", quality=94, subsampling=0)
                    finally:
                        blended.close()
                    add_frame(name, 1 / FPS)
                on_progress(18 + 22 * (index + 1) / max(1, count - 1), f"正在编排转场 {index + 1} / {count - 1}…")
        # The last repeated still gives concat a following timestamp for its hold.
        entries.extend((f"file 'slide-{count-1:04d}.jpg'", f"option framerate {FPS}"))
        (work / "frames.ffconcat").write_text("\n".join(entries) + "\n", encoding="ascii")
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   # safe=0 permits the image demuxer's framerate option. This
                   # manifest contains generated ASCII basenames only, never
                   # user-provided filenames, protocols, or filter fragments.
                   "-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "0", "-i", "frames.ffconcat"]
        if music_path is not None:
            command += ["-protocol_whitelist", "file,pipe", "-stream_loop", "-1", "-i", str(Path(music_path).resolve()),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-af", f"volume={volume:.4f},afade=t=in:st=0:d={min(1.2,duration/3):.3f},afade=t=out:st={max(0,duration-1.5):.3f}:d={min(1.5,duration/3):.3f}"]
        else:
            command += ["-map", "0:v:0", "-an"]
        command += ["-vf", f"fps={FPS},scale=iw:ih:in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "18",
                    "-profile:v", "high", "-level:v", "4.1", "-threads", "4", "-colorspace", "bt709",
                    "-color_trc", "bt709", "-color_primaries", "bt709", "-color_range", "tv",
                    "-t", f"{duration:.6f}", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                    "-f", "mp4", str(output_path.resolve())]
        try:
            _encode(command, work, duration, cancelled, on_progress)
            if not output_path.is_file() or output_path.stat().st_size < 1000:
                raise VideoExportError("视频未能完整保存，请重试。")
            _check_cancelled(cancelled)
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
    return {"duration": round(duration, 3), "width": width, "height": height}
