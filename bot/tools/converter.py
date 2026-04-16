import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {
    # video
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    'mov': 'video/quicktime',
    'avi': 'video/x-msvideo',
    # audio
    'mp3': 'audio/mpeg',
    'wav': 'audio/wav',
    'ogg': 'audio/ogg',
    'flac': 'audio/flac',
    # image
    'gif': 'image/gif',
}

AUDIO_FORMATS = {'mp3', 'wav', 'ogg', 'flac'}

_max_concurrent = int(os.getenv('MAX_CONCURRENT_CONVERSIONS', 3))
_semaphore = asyncio.Semaphore(_max_concurrent)


async def convert_media(input_path: str, output_path: str, file_ext: str) -> str:
    """Convert media file to the target format using FFmpeg.

    Uses asyncio.create_subprocess_exec (not shell) to avoid command injection.
    Returns the output_path on success, raises RuntimeError on failure.
    """
    file_ext = file_ext.lower().lstrip('.')

    if file_ext not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format: {file_ext}")

    # Build args list for create_subprocess_exec (no shell involved)
    args = ['ffmpeg', '-i', input_path, '-y']

    if file_ext in AUDIO_FORMATS:
        args.extend(['-vn'])  # strip video track
        if file_ext == 'mp3':
            args.extend(['-codec:a', 'libmp3lame', '-q:a', '2'])
        elif file_ext == 'flac':
            args.extend(['-codec:a', 'flac'])
        elif file_ext == 'ogg':
            args.extend(['-codec:a', 'libvorbis', '-q:a', '5'])
        elif file_ext == 'wav':
            args.extend(['-codec:a', 'pcm_s16le'])
    elif file_ext == 'gif':
        args.extend(['-vf', 'fps=15,scale=480:-1:flags=lanczos', '-loop', '0'])
    elif file_ext == 'webm':
        args.extend(['-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '0', '-c:a', 'libopus'])
    elif file_ext == 'mov':
        args.extend(['-c:v', 'libx264', '-c:a', 'aac'])
    elif file_ext == 'avi':
        args.extend(['-c:v', 'libx264', '-c:a', 'mp3'])
    elif file_ext == 'mp4':
        args.extend(['-c:v', 'libx264', '-c:a', 'aac'])

    args.append(output_path)

    logger.info(f"Converting {input_path} -> {output_path} (format: {file_ext})")

    async with _semaphore:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("FFmpeg conversion timed out (5 min limit)")

    if process.returncode != 0:
        error_msg = stderr.decode(errors='replace')[-500:]  # last 500 chars of stderr
        logger.error(f"FFmpeg conversion failed (exit {process.returncode}): {error_msg}")
        raise RuntimeError(f"FFmpeg conversion failed: {error_msg}")

    logger.info(f"Conversion complete: {output_path} ({os.path.getsize(output_path) / 1024 / 1024:.1f}MB)")
    return output_path


async def ffprobe_video_metadata(file_path: str) -> Optional[dict]:
    """Probe a media file with ffprobe. Returns {'duration': float, 'width': int, 'height': int} on success,
    or None if the file can't be probed. width/height are 0 when there is no video stream (audio-only files).
    Never raises — caller falls back to whatever metadata they already have."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=width,height,codec_type',
            '-show_entries', 'format=duration',
            '-of', 'json', file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(f"ffprobe timed out for {file_path}")
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None
    except Exception as e:
        logger.warning(f"ffprobe error for {file_path}: {e}")
        return None

    if proc.returncode != 0 or not stdout_b:
        logger.warning(f"ffprobe failed (rc={proc.returncode}) for {file_path}")
        return None

    try:
        probed = json.loads(stdout_b.decode('utf-8', errors='replace'))
    except Exception as e:
        logger.warning(f"ffprobe JSON parse error for {file_path}: {e}")
        return None

    duration = 0.0
    fmt_dur = probed.get('format', {}).get('duration')
    if fmt_dur is not None:
        try:
            duration = float(fmt_dur)
        except (TypeError, ValueError):
            duration = 0.0

    width = 0
    height = 0
    for stream in probed.get('streams', []):
        if stream.get('codec_type') == 'video':
            w = stream.get('width')
            h = stream.get('height')
            if w:
                try:
                    width = int(w)
                except (TypeError, ValueError):
                    pass
            if h:
                try:
                    height = int(h)
                except (TypeError, ValueError):
                    pass
            break

    return {'duration': duration, 'width': width, 'height': height}
