import boto3
from botocore.client import Config
from os import getenv, path
import logging
import asyncio


class CdnSpacesClient:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        session = boto3.session.Session()
        self.client = session.client('s3',
            region_name='nyc3',
            endpoint_url='https://nyc3.digitaloceanspaces.com',
            aws_access_key_id=getenv("cdn_id"),
            aws_secret_access_key=getenv("cdn_sec"),
            config=Config(signature_version='s3v4')
        )

    async def cdn_upload_video(self, file_path, storage_type="temp", content_type="video/mp4") -> tuple[bool, str]:
        filename = path.basename(file_path)
        self.logger.info(f"Uploading video {file_path} to CDN...")
        try:
            # Run sync boto3 upload in a thread pool. boto3.upload_file streams from disk and
            # automatically uses multipart for large files (default 8MB threshold), so memory
            # usage stays flat regardless of file size.
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                self.put_video,
                file_path,
                filename,
                storage_type,
                content_type
            )
            return result
        except Exception as e:
            self.logger.error(f"Error uploading video {file_path}: {str(e)}")
            return False, str(e)

    async def upload_webp(self, file_path: str):
        filename = file_path.split("/")[-1]
        cdn_patj = f"img/{filename}"
        self.logger.info(f"Uploading {filename} to {cdn_patj}")
        try:
            # File read + boto3 put_object are both synchronous; offload to a
            # thread so the event loop isn't blocked for ~100ms-1s per embed.
            await asyncio.to_thread(self._put_webp_sync, file_path, cdn_patj)
            return True, f"https://cdn.clyppy.io/{cdn_patj}"
        except Exception as e:
            self.logger.info(f"Error uploading {filename}: {str(e)}")
            return False, str(e)

    def _put_webp_sync(self, file_path: str, cdn_path: str) -> None:
        with open(file_path, 'rb') as file:
            img_data = file.read()
        self.client.put_object(
            Bucket='clyppy',
            Key=cdn_path,
            Body=img_data,
            ACL='public-read',
            ContentType='image/webp'
        )

    def put_video(self, file_path, filename, storage_type="temp", content_type="video/mp4") -> tuple[bool, str]:
        object_key = f"{storage_type}/{filename}"
        cdn_file_url = f"https://cdn.clyppy.io/{object_key}"
        self.logger.info(f"Uploading {filename} to {cdn_file_url}")

        try:
            self.client.upload_file(
                Filename=file_path,
                Bucket='clyppy',
                Key=object_key,
                ExtraArgs={'ACL': 'public-read', 'ContentType': content_type}
            )
            self.logger.info(f"Uploaded {filename} to {cdn_file_url}")
            return True, cdn_file_url
        except Exception as e:
            self.logger.error(f"Error uploading {filename}: {str(e)}")
            return False, str(e)
