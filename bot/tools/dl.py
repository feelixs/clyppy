from bot.types import DownloadResponse, LocalFileInfo
from bot.errors import UnknownError, VideoTooLongForExtend, VideoTooShortForExtend, VideoExtensionFailed, VideoContainsNSFWContent
from bot.classes import BaseClip, is_discord_compatible, tryremove
from pathlib import Path
from typing import Union
from moviepy import VideoFileClip
import asyncio
import os
import re
import uuid
import json
import fcntl
from contextlib import asynccontextmanager


class DownloadManager:
    def __init__(self, p):
        self._parent = p
        max_concurrent = os.getenv('MAX_RUNNING_AUTOEMBED_DOWNLOADS', 5)
        self._semaphore = asyncio.Semaphore(int(max_concurrent))

    async def download_clip(
            self,
            clip: BaseClip,
            can_send_files=False,
            skip_upload=False,
            extend_with_ai=False
    ) -> Union[DownloadResponse, LocalFileInfo]:
        desired_filename = f'{clip.service}_{clip.clyppy_id}' if clip.service != 'base' else f'{clip.clyppy_id}'
        if len(desired_filename) > 200:
            desired_filename = desired_filename[:200]
        desired_filename += ".mp4"
        async with self._semaphore:
            if not isinstance(clip, BaseClip):
                raise TypeError(f"Invalid clip object passed to download_clip of type {type(clip)}")
            self._parent.logger.info("Run clip.download()")

        if skip_upload or extend_with_ai:
            # force manual override of auto-upload (download() may upload, but dl_download() doesn't)
            r: LocalFileInfo = await clip.dl_download(filename=desired_filename, can_send_files=can_send_files)
        else:
            r: DownloadResponse = await clip.download(filename=desired_filename, can_send_files=can_send_files)

        if extend_with_ai:
            # Create unique filename with _extended suffix (don't overwrite original)
            original_file = r.local_file_path
            file_base = original_file.rsplit('.', 1)[0]  # Remove .mp4 extension
            unique_suffix = str(uuid.uuid4())[:8]  # Use first 8 chars of UUID
            extended_file = f"{file_base}_extended_{unique_suffix}.mp4"
            clip.clyppy_id = clip._generate_clyppy_id(f"{clip.clyppy_id}_extended_{unique_suffix}", low_collision=True)
            self._parent.logger.info(f"Extended video clyppy_id: {clip.clyppy_id}")

            async with self._get_ai_extend_lock():
                new_duration = await self._extend_video_with_ai(original_file, extended_file)

            r.local_file_path = extended_file
            r.duration = new_duration
            r.filesize = os.path.getsize(extended_file)
            r.can_be_discord_uploaded = is_discord_compatible(r.filesize)
            self._parent.logger.info(f"Extended video: {r.filesize} bytes, can_be_discord_uploaded={r.can_be_discord_uploaded}")
            tryremove(original_file)

            if not (r.can_be_discord_uploaded and can_send_files):
                self._parent.logger.info(f"Uploading extended video to clyppy.io...")
                return await clip.upload_to_clyppyio(r)

        if r is None:
            raise UnknownError
        return r

    @asynccontextmanager
    async def _get_ai_extend_lock(self):
        """
        Acquire a system-wide exclusive lock for AI video extension.
        Uses a file-based lock that works across all bot instances in the Docker container.
        Only one AI extend task can run at a time system-wide.
        """
        # Use /tmp for the lock file (shared across all containers in the same system)
        lock_file_path = '/tmp/clyppybot_ai_extend.lock'
        lock_file = None

        try:
            # Open/create the lock file
            lock_file = open(lock_file_path, 'w')

            self._parent.logger.info("Waiting to acquire AI extend lock...")

            # Use run_in_executor to avoid blocking the async event loop
            # fcntl.flock is blocking, so we run it in a thread pool
            await asyncio.get_event_loop().run_in_executor(
                None,
                fcntl.flock,
                lock_file.fileno(),
                fcntl.LOCK_EX  # Exclusive lock
            )

            self._parent.logger.info("AI extend lock acquired")

            # Yield control back to the caller while holding the lock
            yield

        finally:
            # Release the lock and close the file
            if lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    lock_file.close()
                    self._parent.logger.info("AI extend lock released")
                except Exception as e:
                    self._parent.logger.warning(f"Error releasing AI extend lock: {e}")

    async def _extend_video_with_ai(self, input_file: str, output_file: str) -> float:
        """
        Extend a video using AI models with Sora->Veo fallback

        Args:
            input_file: Path to the original video file (will be preserved)
            output_file: Path where the extended video will be saved

        Returns:
            New video duration in seconds

        Raises:
            VideoTooLongForExtend: If video is longer than 60 seconds
            VideoTooShortForExtend: If video is shorter than 6 seconds
            VideoExtensionFailed: If all models fail
        """
        self._parent.logger.info("Extending video with AI...")

        # Try Sora first, then fallback to Veo
        models_to_try = ['sora', 'runway']
        last_error = None
        saved_prompt = None  # Will store the prompt from a failed attempt

        for model in models_to_try:
            try:
                self._parent.logger.info(f"Attempting video extension with model: {model}")

                # Build command with optional manual prompt from previous failure
                cmd = [
                    'python', '-u',  # -u for unbuffered output (real-time streaming)
                    str(Path(__file__).parent.parent / 'scripts/extend_video.py'),
                    input_file,
                    '--output', output_file,  # Save to new file (don't overwrite original)
                    '--model', model,
                    '--duration', '8',
                ]

                # If we have a saved prompt from a previous failed attempt, use it
                if saved_prompt:
                    self._parent.logger.info(f"Using saved prompt from previous attempt: {saved_prompt}")
                    cmd.extend(['--manual-prompt', saved_prompt])

                # Run the extend_video.py script as a subprocess
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                # Read stdout and stderr asynchronously in background tasks
                stdout_lines = []
                stderr_lines = []

                async def read_stream(stream, lines_list, prefix, log_level='info'):
                    async for line in stream:
                        decoded_line = line.decode('utf-8').rstrip()
                        if decoded_line:  # Only log non-empty lines
                            lines_list.append(decoded_line)
                            log_method = getattr(self._parent.logger, log_level)
                            log_method(f"{prefix}: {decoded_line}")

                # Start reading both streams in background tasks
                stdout_task = asyncio.create_task(
                    read_stream(process.stdout, stdout_lines, f"[{model}]", 'info')
                )
                stderr_task = asyncio.create_task(
                    read_stream(process.stderr, stderr_lines, f"[{model} ERR]", 'warning')
                )

                # Wait for process to complete and streams to finish
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)

                stdout_text = '\n'.join(stdout_lines)
                stderr_text = '\n'.join(stderr_lines)

                # Check for errors in output
                if process.returncode != 0:
                    combined_output = stdout_text + stderr_text

                    # Try to extract saved_prompt from error message if it exists
                    # Error format: {"error": "...", "saved_prompt": "..."}
                    if 'saved_prompt' in combined_output and not saved_prompt:
                        self._parent.logger.info(f"Attempting to extract saved_prompt from error output...")
                        try:
                            # Parse the output to find the JSON with saved_prompt
                            # Look for lines that form a JSON object
                            lines = combined_output.split('\n')
                            json_lines = []
                            in_json = False
                            brace_count = 0

                            for line in lines:
                                # Remove logger prefix if present (e.g., "[sora]: ")
                                stripped = line.strip()
                                if ']: ' in stripped:
                                    # Remove everything up to and including the first ']: '
                                    stripped = stripped.split(']: ', 1)[1] if ']: ' in stripped else stripped

                                stripped = stripped.strip()

                                # Check if line contains a JSON opening brace (handle "Fatal error: {" or just "{")
                                if '{' in stripped and not in_json:
                                    # Extract everything from the first '{' onwards
                                    json_start = stripped.find('{')
                                    stripped = stripped[json_start:]
                                    in_json = True
                                    json_lines = [stripped]
                                    brace_count = stripped.count('{') - stripped.count('}')
                                elif in_json:
                                    json_lines.append(stripped)
                                    brace_count += stripped.count('{') - stripped.count('}')

                                    if brace_count == 0:
                                        # Complete JSON object found
                                        json_str = '\n'.join(json_lines)
                                        try:
                                            error_data = json.loads(json_str)
                                            if 'saved_prompt' in error_data:
                                                saved_prompt = error_data['saved_prompt']
                                                self._parent.logger.info(f"Extracted prompt from error: {saved_prompt}")
                                                break
                                        except json.JSONDecodeError:
                                            pass
                                        in_json = False
                                        json_lines = []
                        except Exception as e:
                            self._parent.logger.warning(f"Could not extract saved_prompt from error: {e}")

                    # Check for NSFW content detection
                    if 'NSFW Content Detected' in combined_output or 'VideoContainsNSFWContent' in combined_output:
                        # Extract reason from error message
                        reason_match = re.search(r'Reason: (.+?)(?:\n|$)', combined_output)
                        reason = reason_match.group(1).strip() if reason_match else "Content flagged as inappropriate"
                        self._parent.logger.warning(f"NSFW content detected: {reason}")
                        raise VideoContainsNSFWContent(reason)

                    # Check for duration validation errors
                    if 'Input video is too long' in combined_output:
                        # Extract duration from error message
                        match = re.search(r'Input video is too long: ([\d.]+)s', combined_output)
                        video_dur = float(match.group(1)) if match else 0
                        raise VideoTooLongForExtend(video_dur)

                    if 'Input video is too short' in combined_output:
                        # Extract duration from error message
                        match = re.search(r'Input video is too short: ([\d.]+)s', combined_output)
                        video_dur = float(match.group(1)) if match else 0
                        raise VideoTooShortForExtend(video_dur)

                    # Check for moderation block (Sora-specific)
                    if 'moderation_blocked' in combined_output or 'moderation system' in combined_output or 'content moderation' in combined_output:
                        self._parent.logger.warning(f"Video blocked by {model} moderation, trying next model...")
                        last_error = f"{model} moderation blocked"
                        continue  # Try next model (will use saved_prompt if available)

                    # Other errors - try next model
                    self._parent.logger.warning(f"Video extension failed with {model}: {combined_output}")
                    last_error = combined_output
                    continue  # Try next model (will use saved_prompt if available)

                # Success! Now validate the output is actually playable
                self._parent.logger.info(f"Video extension successful with {model}")

                # Validate the output file exists and has content
                if not os.path.exists(output_file):
                    self._parent.logger.error(f"Output file does not exist: {output_file}")
                    last_error = "Output file not created"
                    continue

                file_size = os.path.getsize(output_file)
                if file_size < 1000:  # Less than 1KB is definitely not a valid video
                    self._parent.logger.error(f"Output file is too small: {file_size} bytes")
                    last_error = f"Output file too small ({file_size} bytes)"
                    continue

                # Validate the video is actually playable by trying to load it and extract a frame
                try:
                    video = VideoFileClip(output_file)

                    # Check basic properties
                    if video.duration is None or video.duration <= 0:
                        video.close()
                        self._parent.logger.error(f"Video has invalid duration: {video.duration}")
                        last_error = f"Invalid video duration: {video.duration}"
                        continue

                    # Try to extract a frame to verify the video is actually playable
                    # If this fails, the video is corrupt even if it has valid metadata
                    try:
                        test_frame = video.get_frame(0)
                        if test_frame is None or test_frame.size == 0:
                            video.close()
                            self._parent.logger.error("Video frame extraction returned empty frame")
                            last_error = "Corrupt video: cannot extract frames"
                            continue
                    except Exception as frame_error:
                        video.close()
                        self._parent.logger.error(f"Cannot extract frame from video: {frame_error}")
                        last_error = f"Corrupt video: {frame_error}"
                        continue

                    new_duration = video.duration
                    video.close()

                    self._parent.logger.info(f"Video validated: {file_size} bytes, {new_duration:.2f}s duration, playable")
                    return new_duration

                except Exception as validation_error:
                    self._parent.logger.error(f"Video validation failed: {validation_error}")
                    last_error = f"Video validation failed: {validation_error}"
                    continue

            except (VideoTooLongForExtend, VideoTooShortForExtend, VideoContainsNSFWContent):
                # Re-raise these immediately, don't try other models
                # These are validation/content errors that won't be fixed by trying a different model
                raise
            except Exception as e:
                self._parent.logger.error(f"Error extending video with {model}: {e}")
                last_error = str(e)
                continue

        # Both models failed
        error_msg = f"Video extension failed with all models. Last error: {last_error}"
        self._parent.logger.error(f"VIDEO EXTENSION FAILED: {error_msg}")
        raise VideoExtensionFailed(error_msg)
