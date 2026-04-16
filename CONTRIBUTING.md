# Contributing to CLYPPY Bot

Thank you for your interest in contributing to CLYPPY! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [How to Contribute](#how-to-contribute)
- [Adding New Platform Support](#adding-new-platform-support)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing Your Changes](#testing-your-changes)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Community Guidelines](#community-guidelines)

## Getting Started

CLYPPY is a Discord bot that embeds videos from 20+ platforms directly in Discord chat.

## Development Setup

### Prerequisites

- Python 3.12 or higher
- FFmpeg (for video processing)
- Git
- A Discord bot token for testing

### Installation

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/clyppy.git
   cd clyppybot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Install FFmpeg:
   - **Linux**: `sudo apt-get install ffmpeg`
   - **macOS**: `brew install ffmpeg`
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)

4. Set up environment variables:
   ```bash
   # Copy the example environment file
   cp .env.example .env
   ```

   Edit `.env` and set:
   - `CONTRIB_INSTANCE=1` (enables Contributor Mode - bypasses API calls)
   - `CLYPP_TOKEN=your_test_bot_token_here` (your Discord bot token)

5. Run the bot:
   ```bash
   python main.py
   ```

   The bot will start in **Contributor Mode** - API calls will be logged instead of executed.

## Project Structure

```
clyppybot/
├── bot/                    # Core bot logic
│   ├── classes.py         # Base classes (BaseMisc, BaseClip, AutoEmbedder)
│   ├── db.py              # Database management (local database for storing Guild settings)
│   ├── env.py             # Configuration constants
│   ├── errors.py          # Custom exceptions
│   ├── setup.py           # Bot initialization
│   ├── io/                # I/O operations (API, CDN, uploads)
│   ├── platforms/         # Platform-specific integrations
│   ├── tools/             # Utility tools (embedder, downloader)
│   └── scripts/           # Helper scripts
├── cogs/                   # Discord bot cogs
│   ├── base.py            # Main commands and event handlers
│   └── watch.py           # Scheduled tasks
└── main.py                 # Entry point
```

## How to Contribute

### Reporting Bugs

1. Check if the bug has already been reported in [Issues](https://github.com/clyppy/clyppy/issues)
2. If not, create a new issue with:
   - Clear descriptive title
   - Steps to reproduce
   - Expected vs actual behavior
   - Discord bot version and platform
   - Relevant logs or screenshots

### Suggesting Features

1. Open an issue with the `enhancement` label
2. Describe the feature and its use case
3. Explain why it would be valuable to users

### Submitting Code Changes

1. Look for issues tagged with `good first issue` or `help wanted`
2. Comment on the issue to let others know you're working on it
3. Follow the [Pull Request Process](#pull-request-process) below

## Adding New Platform Support (varying complexity - see note below)

To add support for a new video platform:

1. Create a new file in `bot/platforms/` (e.g., `newplatform.py`)

2. Implement a class that inherits from `BaseMisc`:

   ```python
   from bot.classes import BaseMisc, BaseClip
   from bot.types import DownloadResponse
   from bot.errors import VideoTooLong, InvalidClipType
   import re
   
   class NewPlatformMisc(BaseMisc):
       """Handler for NewPlatform video embeds"""

       def __init__(self):
           super().__init__(
               name="newplatform",
               url="https://newplatform.com/",
               patterns=[
                   r"https?://(?:www\.)?newplatform\.com/video/([a-zA-Z0-9]+)",
               ],
               quickembed=False  # This feature has been removed from the bot's pitch -> embeds are now solely to be triggered manually via the /embed command
           )

       def parse_clip_url(self, url: str, extended_url_formats=False) -> str:
           """Extracts the video's platform-unique ID from the URL"""
           # Implementation here
           pass

       async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> BaseClip:
            """
                Fetch and validate video. Must match the structure of this template function
            """
            slug = self.parse_clip_url(url)
            if slug is None:
                raise InvalidClipType
            valid, tokens_used, duration = await self.is_shortform(
                url=url,
                basemsg=basemsg,
                cookies=cookies
            )
            if not valid:
                raise VideoTooLong(duration)

            return NewPlatformClip(slug, self.cdn_client, tokens_used, duration)
   
   class NewPlatformClip(BaseClip):
       def __init__(self, slug, cdn_client, tokens_used: int, duration: int):
        self._service = "human_readable_platform_name"
        self._url = f"https://example.com/video/{slug}"
        super().__init__(slug, cdn_client, tokens_used, duration)

       @property
       def service(self) -> str:
           return self._service

       @property
       def url(self) -> str:
           return self._url
   
       async def download(self, filename=None, dlp_format='best', can_send_files=False, cookies=False) -> DownloadResponse:
           """ If needed, override the BaseClip download() function(s) - *see note below """
           ...    
   
   ```
   - NOTE Many platforms that DON'T have files here are already supported by CLYPPY's auto-fallback function which just passes the input URL to [YT-DLP](https://github.com/yt-dlp/yt-dlp)
   - So - [these](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) sites will (sometimes) be supported by CLYPPY already. 
   - Many of them require additional authorization, though - so this is where adding a separate file here and overriding the download function would be necessary.
   - For this reason, adding support for a new platform could be a process and a half. If you're trying to add support for a specific platform and are facing issues, please reach out to me by adding me on discord @feelixs, or joining the support server.

3. Register the platform in `bot/setup.py` by adding it to the platform list

4. Open a Pull Request with `test` branch as the base.
   - I'll need to manually review and test it, since other contributors won't be able to upload to clyppy.io


## Code Style Guidelines

- Follow [PEP 8](https://pep8.org/) Python style guide
- Use type hints for function parameters and return types
- Use `async`/`await` for asynchronous operations
- Write docstrings for classes and functions
- Keep functions focused and modular
- Use descriptive variable and function names
- Handle errors gracefully with try/except blocks
- Use the custom error classes from `bot/errors.py` when appropriate

### Example

```python
async def download_video(url: str, max_size: int = 25) -> str:
    """
    Download video from URL and return local file path.

    Args:
        url: Video URL to download
        max_size: Maximum file size in MB

    Returns:
        Path to downloaded video file

    Raises:
        DownloadError: If download fails
        FileTooLargeError: If video exceeds max_size
    """
    # Implementation
    pass
```

## Testing Your Changes

**IMPORTANT: All contributors MUST test their changes in Contributor Mode before submitting a PR.**

### Setting Up Contributor Mode

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Set the `CONTRIB_INSTANCE` environment variable in your `.env` file:
   ```bash
   CONTRIB_INSTANCE=1
   CLYPP_TOKEN=your_test_bot_token_here
   ```

3. Run the bot locally:
   ```bash
   Docker build -t clyppybot .
   Docker run clyppybot
   ```

### What Contributor Mode Does

When `CONTRIB_INSTANCE=1` is set:
- ✅ All API calls to `clyppy.io` and `felixcreations.com` are **bypassed**
- ✅ API calls are **logged** instead of executed
- ✅ Mock responses are returned for testing
- ✅ **Small videos (<8MB)** can still be embedded directly to Discord
- ✅ Large videos will be logged but not processed (since they require CDN upload)

### Testing Requirements

**Before submitting your PR, please do the following:**

1. ✅ Test locally with your Discord bot in a test server with `CONTRIB_INSTANCE=1`
2. ✅ Verify your changes work as expected in Contributor Mode
3. ✅ Test with urls pointing to small videos (<8MB) if your changes affect video embedding
4. ✅ Ensure the bot starts without errors
5. ✅ Check that any new commands respond correctly or throw exceptions gracefully

**Note:** You won't have access to the clyppy.io API, but Contributor Mode allows you to test:
- Command structure and responses
- Error handling
- Discord interactions
- Bot functionality (without full video processing)

### Manual Testing Checklist

- [ ] `CONTRIB_INSTANCE=1` is set in your `.env` file
- [ ] Bot starts without errors in Contributor Mode
- [ ] Any new commands respond correctly, or throw Exceptions without crashing the bot
- [ ] Tested with small videos (<8MB) if applicable
- [ ] Checked logs to verify API calls are being bypassed correctly
- [ ] When you push to your fork, the Build GitHub workflow completes without errors


## Commit Guidelines

Write clear, descriptive commit messages:

```
Add support for NewPlatform video embeds

- Implement NewPlatformMisc class with URL parsing
- Add regex patterns for video URL detection
- Handle authentication and cookie requirements
- Add error handling for rate limits
```


## Pull Request Process

1. **Fork** the repository to your GitHub account

2. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make your changes** following the guidelines above

4. **Commit your changes** with clear commit messages

5. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request** into the test branch with:
   - Clear title describing the change
   - Description of what changed and why
   - Reference to related issues (e.g., "Fixes #123")
   - Screenshots or examples if applicable

7. **Respond to feedback** from maintainers and update your PR as needed

8. **Wait for approval** - maintainers will review and merge when ready

### PR Checklist

- [ ] Code follows project style guidelines
- [ ] Changes have been tested locally
- [ ] No unnecessary dependencies added
- [ ] Documentation updated if needed
- [ ] Commit messages are clear and descriptive

## Community Guidelines

- Be respectful and constructive in discussions
- Welcome newcomers and help them get started
- Focus on the code, not the person
- Assume good intentions
- Give credit where credit is due
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md) (if applicable)

## Questions?

If you have questions about contributing:
- Open an issue with the `question` label
- Check existing issues and discussions
- Review the [README.md](README.md) for general information

Thank you for contributing to CLYPPY!
