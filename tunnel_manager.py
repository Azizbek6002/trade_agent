import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from config import config, BASE_DIR

logger = logging.getLogger(__name__)

TUNNEL_LOG = BASE_DIR / "logs" / "tunnel.log"
CLOUDFLARED_BIN = BASE_DIR / "cloudflared"

class TunnelManager:
    """
    Self-Healing Tunnel Watchdog.
    Keeps the Cloudflare HTTP/2 tunnel alive 24/7.
    If the tunnel disconnects or URL expires, automatically restarts it,
    extracts the new URL, updates .env, and refreshes the Telegram Bot menu button.
    """

    def __init__(self):
        self.current_url = config.WEBAPP_URL
        self._process = None

    def get_running_tunnel_pid(self) -> int | None:
        try:
            out = subprocess.check_output(["pgrep", "-f", "cloudflared.*tunnel"], text=True)
            pids = [int(p) for p in out.strip().split() if p]
            return pids[0] if pids else None
        except Exception:
            return None

    def start_tunnel_process(self) -> str | None:
        """Starts cloudflared with http2 protocol and returns the generated URL."""
        if not CLOUDFLARED_BIN.exists():
            logger.warning(f"cloudflared binary not found at {CLOUDFLARED_BIN}")
            return None

        # Terminate any existing stalled cloudflared processes
        pid = self.get_running_tunnel_pid()
        if pid:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

        TUNNEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        # Empty old tunnel log
        with open(TUNNEL_LOG, "w") as f:
            f.write("")

        cmd = [
            str(CLOUDFLARED_BIN),
            "tunnel",
            "--protocol", "http2",
            "--url", f"http://localhost:{config.WEBAPP_PORT}"
        ]

        logger.info("Starting Cloudflare HTTP/2 Tunnel daemon...")
        with open(TUNNEL_LOG, "a") as out:
            self._process = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=out,
                start_new_session=True
            )

        # Wait for URL to appear in log
        for _ in range(15):
            import time
            time.sleep(1)
            url = self.extract_url_from_log()
            if url:
                self.update_env_url(url)
                logger.info(f"🟢 Cloudflare Tunnel online: {url}")
                return url

        logger.warning("Tunnel started but URL extraction timed out.")
        return None

    def extract_url_from_log(self) -> str | None:
        if not TUNNEL_LOG.exists():
            return None
        try:
            content = TUNNEL_LOG.read_text()
            matches = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", content)
            return matches[-1] if matches else None
        except Exception:
            return None

    def update_env_url(self, new_url: str):
        self.current_url = new_url
        env_path = BASE_DIR / ".env"
        if not env_path.exists():
            return
        lines = env_path.read_text().splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith("WEBAPP_URL="):
                new_lines.append(f"WEBAPP_URL={new_url}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"WEBAPP_URL={new_url}")
        env_path.write_text("\n".join(new_lines) + "\n")
        logger.info(f"Updated .env WEBAPP_URL to {new_url}")

    async def update_telegram_menu_button(self, bot):
        """Refreshes the Telegram Menu Button for admin with the latest URL."""
        if not config.ADMIN_TELEGRAM_ID:
            return
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        try:
            admin_url = f"{self.current_url}?admin_key={config.ADMIN_TELEGRAM_ID}"
            await bot.set_chat_menu_button(
                chat_id=config.ADMIN_TELEGRAM_ID,
                menu_button=MenuButtonWebApp(text="RTB App", web_app=WebAppInfo(url=admin_url))
            )
            logger.info("Refreshed Admin Telegram Menu Button with active tunnel URL.")
        except Exception as e:
            logger.warning(f"Could not update Telegram menu button: {e}")

    async def check_tunnel_healthy(self) -> bool:
        """
        Directly checks local cloudflared metrics endpoint (127.0.0.1:20241/ready).
        Returns True if cloudflared is connected to Cloudflare Edge with ready connections.
        """
        import json
        import urllib.request
        try:
            loop = asyncio.get_running_loop()
            def _check():
                req = urllib.request.Request("http://127.0.0.1:20241/ready", headers={"User-Agent": "RTB-Watchdog"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.getcode() == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        return data.get("readyConnections", 0) > 0
                return False
            return await loop.run_in_executor(None, _check)
        except Exception:
            return False

    async def start_watchdog(self, bot=None):
        """
        Runs periodic health check every 30s.
        Auto-restarts tunnel ONLY after 3 consecutive failures to avoid flapping and changing URLs unnecessarily.
        """
        logger.info("Tunnel Watchdog started (self-healing mode with local metrics probe).")
        consecutive_failures = 0
        while True:
            await asyncio.sleep(30)
            pid = self.get_running_tunnel_pid()
            if not pid:
                logger.warning("Cloudflared process is not running. Starting tunnel...")
                loop = asyncio.get_running_loop()
                new_url = await loop.run_in_executor(None, self.start_tunnel_process)
                if new_url and bot:
                    await self.update_telegram_menu_button(bot)
                consecutive_failures = 0
                continue

            is_healthy = await self.check_tunnel_healthy()
            if is_healthy:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logger.warning(f"Tunnel health check failed ({consecutive_failures}/3).")
                if consecutive_failures >= 3:
                    logger.warning("Tunnel persistently unreachable for >90s. Self-healing restart triggered...")
                    loop = asyncio.get_running_loop()
                    new_url = await loop.run_in_executor(None, self.start_tunnel_process)
                    if new_url and bot:
                        await self.update_telegram_menu_button(bot)
                    consecutive_failures = 0


tunnel_manager = TunnelManager()
