import asyncio
import json
import logging
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from config import config, BASE_DIR

logger = logging.getLogger(__name__)

TUNNEL_LOG = BASE_DIR / "logs" / "tunnel.log"
CLOUDFLARED_BIN = BASE_DIR / "cloudflared"
NGROK_BIN = BASE_DIR / "ngrok"


class TunnelManager:
    """
    Self-Healing Tunnel Watchdog (RTB v2.0).
    Supports:
    1. Ngrok Permanent Static Domain (Zero downtime, fixed URL forever).
    2. Cloudflare HTTP/2 Quick Tunnel (Fallback).
    """

    def __init__(self):
        self.provider = config.TUNNEL_PROVIDER
        self.current_url = config.WEBAPP_URL
        self._process = None

    def get_running_tunnel_pid(self) -> int | None:
        try:
            pattern = "ngrok.*http" if self.provider == "ngrok" else "cloudflared.*tunnel"
            out = subprocess.check_output(["pgrep", "-f", pattern], text=True)
            pids = [int(p) for p in out.strip().split() if p]
            return pids[0] if pids else None
        except Exception:
            return None

    def kill_stale_tunnels(self):
        for pattern in ["ngrok", "cloudflared"]:
            try:
                out = subprocess.check_output(["pgrep", "-f", pattern], text=True)
                for pid_str in out.strip().split():
                    if pid_str:
                        try:
                            os.kill(int(pid_str), 9)
                        except Exception:
                            pass
            except Exception:
                pass

    def start_tunnel_process(self) -> str | None:
        """Starts either Ngrok or Cloudflare tunnel according to configuration."""
        self.kill_stale_tunnels()
        TUNNEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TUNNEL_LOG, "w") as f:
            f.write("")

        # 1. Prefer Ngrok Permanent Domain if configured
        if self.provider == "ngrok" and config.NGROK_DOMAIN and NGROK_BIN.exists():
            fixed_url = f"https://{config.NGROK_DOMAIN}"
            cmd = [
                str(NGROK_BIN),
                "http",
                str(config.WEBAPP_PORT),
                f"--url={fixed_url}",
                "--log=stdout"
            ]
            logger.info(f"Starting Ngrok permanent static tunnel ({fixed_url})...")
            with open(TUNNEL_LOG, "a") as out:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=out,
                    stderr=out,
                    start_new_session=True
                )
            import time
            time.sleep(2)
            self.update_env_url(fixed_url)
            logger.info(f"🟢 Ngrok Permanent Static Tunnel online: {fixed_url}")
            return fixed_url

        # 2. Fallback to Cloudflare HTTP/2
        if CLOUDFLARED_BIN.exists():
            cmd = [
                str(CLOUDFLARED_BIN),
                "tunnel",
                "--metrics", "127.0.0.1:20241",
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

            for _ in range(15):
                import time
                time.sleep(1)
                url = self.extract_url_from_log()
                if url:
                    self.update_env_url(url)
                    logger.info(f"🟢 Cloudflare Tunnel online: {url}")
                    return url

        logger.warning("Tunnel start timed out.")
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
            logger.info(f"Refreshed Admin Telegram Menu Button with {admin_url}")
        except Exception as e:
            logger.warning(f"Could not update Telegram menu button: {e}")

    async def check_tunnel_healthy(self) -> bool:
        """Checks local metrics / API endpoint to confirm tunnel edge connection."""
        loop = asyncio.get_running_loop()

        def _probe():
            try:
                if self.provider == "ngrok":
                    req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels", headers={"User-Agent": "RTB-Watchdog"})
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        if resp.getcode() == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            tunnels = data.get("tunnels", [])
                            return len(tunnels) > 0
                    return False
                else:
                    req = urllib.request.Request("http://127.0.0.1:20241/ready", headers={"User-Agent": "RTB-Watchdog"})
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        if resp.getcode() == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            return data.get("readyConnections", 0) > 0
                    return False
            except Exception:
                return False

        return await loop.run_in_executor(None, _probe)

    async def start_watchdog(self, bot=None):
        """
        Runs periodic health check every 30s.
        Auto-restarts tunnel ONLY after 3 consecutive failures.
        """
        logger.info(f"Tunnel Watchdog started (provider: {self.provider}).")
        consecutive_failures = 0
        while True:
            await asyncio.sleep(30)
            pid = self.get_running_tunnel_pid()
            if not pid:
                logger.warning(f"{self.provider} process is not running. Launching tunnel...")
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
                logger.warning(f"Tunnel health check notice ({consecutive_failures}/3).")
                if consecutive_failures >= 3:
                    logger.warning("Tunnel persistently unreachable for >90s. Self-healing restart triggered...")
                    loop = asyncio.get_running_loop()
                    new_url = await loop.run_in_executor(None, self.start_tunnel_process)
                    if new_url and bot:
                        await self.update_telegram_menu_button(bot)
                    consecutive_failures = 0


tunnel_manager = TunnelManager()

