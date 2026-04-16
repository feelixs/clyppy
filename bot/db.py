import sqlite3
import logging
from contextlib import contextmanager
from typing import Any, Callable, List, Tuple, Optional
from bot.env import POSSIBLE_TOO_LARGE, POSSIBLE_ON_ERRORS

logger = logging.getLogger(__name__)

# All valid quickembed platform identifiers (lowercase)
VALID_QUICKEMBED_PLATFORMS = [
    'twitch', 'kick', 'insta', 'medal', 'reddit', 'facebook',
    'yt', 'x', 'bsky', 'tiktok', 'r34', 'xvid', 'phub',
    'youp', 'vimeo', 'bili', 'dailymotion', 'drive', 'dsc'
]
DEFAULT_QUICKEMBED_PLATFORMS = 'insta,tiktok,twitch,kick,medal'

# Map platform_name to short identifier
PLATFORM_NAME_TO_ID = {
    'Twitch': 'twitch', 'Kick': 'kick', 'Instagram': 'insta',
    'Medal': 'medal', 'Reddit': 'reddit', 'Facebook': 'facebook',
    'YouTube': 'yt', 'Twitter': 'x', 'BlueSky': 'bsky',
    'TikTok': 'tiktok', 'Rule34Video': 'r34', 'Xvideos': 'xvid',
    'PornHub': 'phub', 'YouPorn': 'youp', 'Vimeo': 'vimeo',
    'Bilibili': 'bili', 'Dailymotion': 'dailymotion',
    'Google Drive': 'drive', 'Discord': 'dsc'
}


class DbResponseFormat:
    def __init__(self, possible_values: List[str], stored_int: int):
        self.all_values = possible_values
        self.id = stored_int
        self.setting_str = possible_values[self.id]

    def __str__(self):
        return self.setting_str


class GuildDatabase:
    def __init__(self, db_path: str = "guild_settings.db", on_save: Callable = None, on_load: Callable = None):
        self.db_path = db_path
        self.on_save = on_save
        self.on_load = on_load

    async def save(self):
        """Save database to server if callback exists."""
        if self.on_save:
            logger.info("Saving database to the server...")
            await self.on_save()

    @contextmanager
    def get_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    async def setup_db(self):
        logger.info("Setting up database...")
        """Initialize the database with required tables and load from server."""
        # Load from server if callback exists
        if self.on_load:
            logger.info("Loading database from the server...")
            await self.on_load()

        with self.get_db() as conn:
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS guild_settings (
                                guild_id INTEGER PRIMARY KEY,
                                setting TEXT
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS error_channel (
                                guild_id INTEGER PRIMARY KEY,
                                channel INTEGER
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS embed_buttons (
                                guild_id INTEGER PRIMARY KEY,
                                setting INTEGER
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS embed_enabled (
                                guild_id INTEGER NOT NULL,
                                channel_id INTEGER,
                                setting TEXT NOT NULL,
                                UNIQUE(guild_id, channel_id)
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS nsfw_enabled (
                                guild_id INTEGER PRIMARY KEY,
                                setting BOOLEAN
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS auto_delete (
                                guild_id INTEGER PRIMARY KEY,
                                setting BOOLEAN
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS welcome_dm_sent (
                                user_id INTEGER PRIMARY KEY,
                                sent_at TIMESTAMP NOT NULL
                            )
                        ''')
            conn.execute('''
                            CREATE TABLE IF NOT EXISTS bot_state (
                                key TEXT PRIMARY KEY,
                                value TEXT NOT NULL
                            )
                        ''')

            # Migration: Convert boolean embed_enabled to TEXT if needed
            try:
                cursor = conn.execute('PRAGMA table_info(embed_enabled)')
                columns = {c[1]: c[2] for c in cursor.fetchall()}

                if 'setting' in columns and columns['setting'].upper() != 'TEXT':
                    # Old schema detected (BOOLEAN or other non-TEXT type) - migrate
                    logger.info(f"Migrating embed_enabled from {columns['setting']} to TEXT...")
                    cursor = conn.execute('SELECT guild_id, setting FROM embed_enabled')
                    rows = cursor.fetchall()

                    conn.execute('DROP TABLE embed_enabled')
                    conn.execute('''
                        CREATE TABLE embed_enabled (
                            guild_id INTEGER PRIMARY KEY,
                            setting TEXT
                        )
                    ''')

                    for guild_id, old_val in rows:
                        new_val = 'twitch,kick' if old_val else 'none'
                        conn.execute('INSERT INTO embed_enabled VALUES (?, ?)',
                                     (guild_id, new_val))
                    logger.info(f"Migration complete: {len(rows)} guilds migrated")
            except Exception as e:
                logger.error(f"Migration check failed: {e}")

            # Migration: Add channel_id column for channel-level quickembed settings
            try:
                cursor = conn.execute('PRAGMA table_info(embed_enabled)')
                columns = {c[1]: c for c in cursor.fetchall()}

                if 'channel_id' not in columns:
                    logger.info("Migrating embed_enabled to support channel-level settings...")

                    # Read existing guild-level settings
                    cursor = conn.execute('SELECT guild_id, setting FROM embed_enabled')
                    rows = cursor.fetchall()

                    # Drop and recreate table with new schema
                    conn.execute('DROP TABLE embed_enabled')
                    conn.execute('''
                        CREATE TABLE embed_enabled (
                            guild_id INTEGER NOT NULL,
                            channel_id INTEGER,
                            setting TEXT NOT NULL,
                            UNIQUE(guild_id, channel_id)
                        )
                    ''')

                    # Re-insert all settings with channel_id = NULL (guild-level)
                    for guild_id, setting in rows:
                        conn.execute(
                            'INSERT INTO embed_enabled (guild_id, channel_id, setting) VALUES (?, NULL, ?)',
                            (guild_id, setting)
                        )

                    logger.info(f"Channel-level migration complete: {len(rows)} guild settings migrated")
            except Exception as e:
                logger.error(f"Channel-level migration check failed: {e}")

            conn.commit()

    def get_nsfw_enabled(self, guild_id) -> bool:
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT setting FROM nsfw_enabled WHERE guild_id = ?',
                    (guild_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else False
        except sqlite3.Error as e:
            logger.error(f"Database error when getting nsfw_enabled for guild {guild_id}: {e}")
            return False  # default = false

    def set_nsfw_enabled(self, guild_id: int, new: bool):
        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO nsfw_enabled (guild_id, setting)
                    VALUES (?, ?)
                ''', (guild_id, new))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting nsfw_enabled for guild {guild_id}: {e}")
            return False

    def get_auto_delete(self, guild_id) -> bool:
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT setting FROM auto_delete WHERE guild_id = ?',
                    (guild_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else False
        except sqlite3.Error as e:
            logger.error(f"Database error when getting auto_delete for guild {guild_id}: {e}")
            return False  # default = false

    def set_auto_delete(self, guild_id: int, new: bool) -> bool:
        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO auto_delete (guild_id, setting)
                    VALUES (?, ?)
                ''', (guild_id, new))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting auto_delete for guild {guild_id}: {e}")
            return False

    def _parse_quickembed_setting(self, setting: str) -> List[str]:
        """Helper to parse 'none', 'all', or comma-separated platforms."""
        if setting == 'none':
            return []
        if setting == 'all':
            return ['all']
        return [p.strip().lower() for p in setting.split(',')
                if p.strip().lower() in VALID_QUICKEMBED_PLATFORMS]

    def get_quickembed_platforms(self, guild_id, channel_id=None) -> Tuple[List[str], bool]:
        """
        Returns list of enabled platform IDs for this guild/channel.

        Hierarchical fallback:
        1. If channel_id provided, check channel-specific setting
        2. Fall back to guild-level setting (channel_id IS NULL)
        3. Fall back to default

        Returns: (platforms_list, is_default)
        """
        try:
            with self.get_db() as conn:
                # Try channel-specific first if provided
                if channel_id is not None:
                    cursor = conn.execute(
                        'SELECT setting FROM embed_enabled WHERE guild_id = ? AND channel_id = ?',
                        (guild_id, channel_id)
                    )
                    result = cursor.fetchone()
                    if result and result[0]:
                        # Channel-specific setting found
                        return self._parse_quickembed_setting(result[0]), False

                # Fall back to guild-level (channel_id = 0)
                cursor = conn.execute(
                    'SELECT setting FROM embed_enabled WHERE guild_id = ? AND channel_id = 0',
                    (guild_id,)
                )
                result = cursor.fetchone()

                if not result or not result[0]:
                    return DEFAULT_QUICKEMBED_PLATFORMS.split(','), True

                return self._parse_quickembed_setting(result[0]), False

        except sqlite3.Error as e:
            logger.error(f"Database error when getting quickembed_platforms for guild {guild_id}: {e}")
            return DEFAULT_QUICKEMBED_PLATFORMS.split(','), True

    def is_platform_quickembed_enabled(self, guild_id, platform_name: str, channel_id=None) -> bool:
        """Check if platform is enabled for quickembeds in this channel/guild."""
        platform_id = PLATFORM_NAME_TO_ID.get(platform_name, platform_name.lower())
        p, _ = self.get_quickembed_platforms(guild_id, channel_id)
        return 'all' in p or platform_id in p

    def set_quickembed_platforms(self, guild_id: int, platforms_str: str, channel_id: Optional[int] = None) -> Tuple[bool, Optional[str], Optional[List[str]]]:
        """
        Set quickembed platforms for a guild or specific channel.

        Args:
            guild_id: Discord guild ID
            platforms_str: 'none', 'all', or comma-separated platform names
            channel_id: Optional channel ID (None = guild-level, stored as 0)

        Returns: (success, error_msg, valid_platforms)
        """
        if channel_id is None:
            channel_id = 0
        platforms_str = platforms_str.strip()

        if platforms_str.lower() == 'none':
            normalized, valid = 'none', []
        elif platforms_str.lower() == 'all':
            normalized, valid = 'all', VALID_QUICKEMBED_PLATFORMS.copy()
        else:
            # Build reverse mapping: lowercase friendly name -> short id
            name_to_id = {name.lower(): id for name, id in PLATFORM_NAME_TO_ID.items()}

            requested = [p.strip() for p in platforms_str.split(',')]
            valid = []
            invalid = []

            for p in requested:
                if not p:
                    continue
                p_lower = p.lower()
                # Check if it's a friendly name first, then short identifier
                if p_lower in name_to_id:
                    valid.append(name_to_id[p_lower])
                elif p_lower in VALID_QUICKEMBED_PLATFORMS:
                    valid.append(p_lower)
                else:
                    invalid.append(p)

            if invalid:
                return False, f"Invalid platform(s): {', '.join(invalid)}. Valid options: {', '.join(PLATFORM_NAME_TO_ID.keys())}, 'all', or 'none'", None
            if not valid:
                return False, "No valid platforms specified", None
            normalized = ','.join(valid)

        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT INTO embed_enabled (guild_id, channel_id, setting)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, channel_id)
                    DO UPDATE SET setting = excluded.setting
                ''', (guild_id, channel_id, normalized))
                conn.commit()
                return True, None, valid
        except sqlite3.Error as e:
            logger.error(f"Database error when setting quickembed_platforms for guild {guild_id}: {e}")
            return False, f"Database error: {e}", None

    def delete_channel_quickembed_setting(self, guild_id: int, channel_id: int) -> bool:
        """Remove channel-specific override, reverting to guild-level setting."""
        try:
            with self.get_db() as conn:
                conn.execute(
                    'DELETE FROM embed_enabled WHERE guild_id = ? AND channel_id = ?',
                    (guild_id, channel_id)
                )
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error deleting channel quickembed setting: {e}")
            return False

    def list_channel_overrides(self, guild_id: int) -> List[Tuple[int, str]]:
        """
        Get all channel-specific overrides for a guild.

        Returns: List of (channel_id, setting) tuples
        """
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT channel_id, setting FROM embed_enabled WHERE guild_id = ? AND channel_id != 0',
                    (guild_id,)
                )
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Error listing channel overrides: {e}")
            return []

    def get_embed_buttons(self, guild_id: int) -> int:
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT setting FROM embed_buttons WHERE guild_id = ?',
                    (guild_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"Database error when getting embed_buttons for guild {guild_id}: {e}")
            return 0

    def set_embed_buttons(self, guild_id: int, new_setting: int) -> bool:
        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO embed_buttons (guild_id, setting)
                    VALUES (?, ?)
                ''', (guild_id, new_setting))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting embed_buttons for guild {guild_id}: {e}")
            return False

    def get_error_channel(self, guild_id: int) -> int:
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT channel FROM error_channel WHERE guild_id = ?',
                    (guild_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        except sqlite3.Error as e:
            logger.error(f"Database error when getting error channel for guild {guild_id}: {e}")
            return 0

    def set_error_channel(self, guild_id: int, channel_id: int) -> bool:
        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO error_channel (guild_id, channel)
                    VALUES (?, ?)
                ''', (guild_id, channel_id))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting error channel for guild {guild_id}: {e}")
            return False

    def get_setting(self, guild_id: int) -> str:
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT setting FROM guild_settings WHERE guild_id = ?',
                    (guild_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else "00"
        except sqlite3.Error as e:
            logger.error(f"Database error when getting setting for guild {guild_id}: {e}")
            return "00"

    def get_setting_str(self, guild_id):
        sett = self.get_setting(guild_id)

        # translate to words
        if sett is None:
            settings = POSSIBLE_TOO_LARGE[0], POSSIBLE_ON_ERRORS[0]
        else:
            settings = POSSIBLE_TOO_LARGE[int(sett[0])], POSSIBLE_ON_ERRORS[int(sett[1])]
        return (#f"**too_large**: {settings[0]}\n"
                f"**on_error**: {settings[1]}")

    def get_too_large(self, guild_id) -> DbResponseFormat:
        this_pos = POSSIBLE_TOO_LARGE
        s = self.get_setting(guild_id)
        if s is None:
            return this_pos[0]
        this_setting = int(s[0])
        return DbResponseFormat(POSSIBLE_TOO_LARGE, this_setting)

    def get_on_error(self, guild_id) -> DbResponseFormat:
        on_er = POSSIBLE_ON_ERRORS
        s = self.get_setting(guild_id)
        if s is None:
            return on_er[0]
        error_setting = int(s[1])
        return DbResponseFormat(POSSIBLE_ON_ERRORS, error_setting)

    def is_dm_on_error(self, guild_id) -> bool:
        return str(self.get_on_error(guild_id)) == "dm"

    def is_trim_enabled(self, guild_id) -> bool:
        return str(self.get_too_large(guild_id)) == "trim"

    def set_setting(self, guild_id: int, value: Any) -> bool:
        """Set or update setting for a specific guild."""
        try:
            with self.get_db() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO guild_settings (guild_id, setting)
                    VALUES (?, ?)
                ''', (guild_id, str(value)))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting value for guild {guild_id}: {e}")
            return False

    def has_received_welcome_dm(self, user_id: int) -> bool:
        """Check if user has already received welcome DM."""
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    'SELECT user_id FROM welcome_dm_sent WHERE user_id = ?',
                    (user_id,)
                )
                result = cursor.fetchone()
                return result is not None
        except sqlite3.Error as e:
            logger.error(f"Database error when checking welcome DM for user {user_id}: {e}")
            # Fail-safe: assume DM was sent to avoid spam on database errors
            return True

    def record_welcome_dm_sent(self, user_id: int) -> bool:
        """Record that a user received welcome DM."""
        try:
            with self.get_db() as conn:
                from datetime import datetime
                conn.execute(
                    'INSERT OR IGNORE INTO welcome_dm_sent (user_id, sent_at) VALUES (?, ?)',
                    (user_id, datetime.now().isoformat())
                )
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when recording welcome DM for user {user_id}: {e}")
            return False

    def get_bot_state(self, key: str) -> Optional[str]:
        """Get a persistent bot state value by key."""
        try:
            with self.get_db() as conn:
                cursor = conn.execute('SELECT value FROM bot_state WHERE key = ?', (key,))
                result = cursor.fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            logger.error(f"Database error when getting bot_state '{key}': {e}")
            return None

    def set_bot_state(self, key: str, value: str) -> bool:
        """Set a persistent bot state value by key."""
        try:
            with self.get_db() as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)',
                    (key, value)
                )
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error when setting bot_state '{key}': {e}")
            return False
