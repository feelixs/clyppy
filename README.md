# CLYPPY Discord Bot

![Tests (Production)](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/feelixs/0f3976a2689d282cd097548a2726d706/raw/badge.json)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Discord Bots](https://top.gg/api/widget/upvotes/1111723928604381314.svg)](https://top.gg//bot/1111723928604381314)

This is the official GitHub repository for the CLYPPY Discord bot. Add it to your Discord Server for easy video link embeds! [clyppy.io/invite](https://clyppy.io/invite)

Clyppy turns video links from 20+ platforms into instant Discord uploads! Watch Twitch clips, YouTube videos, TikToks and more directly in chat without leaving Discord.

![](https://clyppy.io/static/prev/embedcommand.gif)

## Features

- **20+ Platform Support**: Twitch, YouTube, TikTok, Instagram, Reddit, Twitter/X, Facebook, and more
- **High-Quality Embeds**: Full HD video embeds directly in Discord
- **Easy to Use**: Simple `/embed` command interface
- **Fast Processing**: Optimized video download and upload pipeline
- **User Profiles & Rankings**: Track your embed usage and compete on leaderboards
- **Server Customization**: Configure embed behavior per server

## Quick Start

### For Users

Simply [invite CLYPPY to your Discord server](https://clyppy.io/invite) and use the `/embed` command with any supported video link!

**Required Permissions:**
- Attach Files
- Send Messages
- Embed Links

**Discord Settings:**
Make sure in your personal Discord settings you've enabled "Link Embeds" under Chat > Display Images, Videos, and LOLCats

## Development Setup

### ⚠️ Important Notice for Contributors

**This bot requires access to private API infrastructure (`clyppy.io` and `felixcreations.com`) that is not publicly available** - but the source code is open for transparency, education, and contributions.

**What you CAN do:**

As a contributor you can still run a test instance of the bot to confirm funcionality. Set the environment variable `CONTRIB_INSTANCE=1` in the Dockerfile before building. This variable will enable contributor mode, where the bot will log certain events instead of calling its external API. For example, instead of uploading a video to clyppy.io, it will send a log. What this means:

- All **large videos** will not be processed by a contributor bot instance
- All **small videos** (below 8mb) will be processed normally and uploaded to Discord.


### Prerequisites

- Python 3.12 or higher
- Docker
- Discord Bot Token (for testing)
- Git

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/feelixs/clyppy.git
   cd clyppybot
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Docker:**
   - Follow the online instructions depending on your OS

4. **Set up environment variables:**

   Copy `.env.example` into a new `.env` file and fill in the required variables:
   ```bash
   CLYPP_TOKEN=your_discord_bot_token_here
   CONTRIB_INSTANCE=1
   ```

5. **Run the bot:**
   ```bash
   Docker build -t clyppybot .
   Docker run clyppybot
   ```

### Project Structure

```
clyppybot/
├── bot/                    # Core bot logic
│   ├── classes.py         # Base classes (BaseMisc, BaseClip, AutoEmbedder)
│   ├── db.py              # Database management
│   ├── env.py             # Configuration constants
│   ├── errors.py          # Custom exceptions
│   ├── io/                # I/O operations (API, CDN, uploads)
│   ├── platforms/         # Platform-specific integrations
│   ├── tools/             # Utility tools (embedder, downloader)
│   └── scripts/           # Helper scripts
├── cogs/                   # Discord bot cogs
│   ├── base.py            # Main commands and event handlers
│   └── watch.py           # Scheduled tasks
├── token-giver/            # Separate bot that processes joins to the Support Server, gifting tokens when someone new joins
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
└── README.md              
```

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

**Good first contributions:**
- Bug reports with detailed reproduction steps
- Documentation improvements
- Adding support for new video platforms (requires testing coordination)
- Code quality improvements

**Note:** Pull requests that add new platform support should target the `test` branch, as they require maintainers to manually test using an instance with API access.

## Supported Platforms

Clyppy supports 20+ video platforms including:

- Twitch
- YouTube
- TikTok
- Instagram
- Reddit
- Twitter/X
- Facebook
- Kick
- Medal.tv
- Vimeo
- Dailymotion
- Bluesky
- Bilibili
- Google Drive videos
- And more via yt-dlp fallback

See [CONTRIBUTING.md](CONTRIBUTING.md) for information about adding new platforms.

## ⚠️ Content Warning

This bot includes support for embedding videos from NSFW platforms. Server administrators should:
- Enable NSFW embeds only in age-restricted channels
- Comply with Discord's Terms of Service regarding NSFW content
- Configure appropriate permissions and channel restrictions

## Support

- **Support Server**: [Join our Discord](https://discord.gg/Xts5YMUbeS)
- **Vote for CLYPPY**: [clyppy.io/vote](https://clyppy.io/vote/)
- **Invite CLYPPY**: [clyppy.io/invite](https://clyppy.io/invite/)
- **Issues**: [GitHub Issues](https://github.com/feelixs/clyppy/issues)

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [interactions.py](https://github.com/interactions-py/interactions.py)
- Video downloads powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- Video processing with [FFmpeg](https://ffmpeg.org/) and [MoviePy](https://zulko.github.io/moviepy/)

## Privacy & Security

- The bot does not store message content
- Video processing is done server-side and files are automatically cleaned up
- User data (embed counts, tokens) is stored securely
- See our [privacy policy](https://clyppy.io/privacy) for more information

---

**Made with ❤️ by [feelixs](https://github.com/feelixs)**

