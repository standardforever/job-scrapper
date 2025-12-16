import asyncio
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiohttp
from playwright.async_api import Browser, Page, Playwright, async_playwright


# =============================================================================
# Chrome CDP Manager
# =============================================================================


@dataclass
class ChromeConfig:
    port: int = 9222
    startup_timeout: int = 20
    health_check_interval: float = 1.0
    health_check_timeout: float = 1.0
    chrome_paths: list[str] = field(default_factory=lambda: [
         # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",

        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",

        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "chrome",
        "chromium",

    ])
    chrome_args: list[str] = field(default_factory=lambda: [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
    ])


class ChromeCDPManager:
    def __init__(self, config: Optional[ChromeConfig] = None):
        self.config = config or ChromeConfig()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._user_data_dir: Optional[str] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    @property
    def cdp_url(self) -> str:
        return f"http://localhost:{self.config.port}"

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    @property
    def page(self) -> Optional[Page]:
        return self._page

    async def _find_chrome_executable(self) -> str:
        for path in self.config.chrome_paths:
            if not os.path.exists(path) and path not in ["chrome", "chromium"]:
                continue

            try:
                proc = await asyncio.create_subprocess_exec(
                    path,
                    "--version",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                await proc.wait()
                return path
            except Exception:
                continue

        raise RuntimeError("Chrome not found. Please install Chrome or Chromium.")

    async def _wait_for_cdp_ready(self) -> bool:
        timeout = aiohttp.ClientTimeout(total=self.config.health_check_timeout)

        for _ in range(self.config.startup_timeout):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.cdp_url}/json/version",
                        timeout=timeout,
                    ) as response:
                        if response.status == 200:
                            return True
            except Exception:
                pass

            await asyncio.sleep(self.config.health_check_interval)

        return False

    async def start_chrome(self) -> asyncio.subprocess.Process:
        if self._process is not None:
            raise RuntimeError("Chrome is already running.")

        self._user_data_dir = tempfile.mkdtemp(prefix="chrome_cdp_")
        chrome_exe = await self._find_chrome_executable()

        cmd = [
            chrome_exe,
            f"--remote-debugging-port={self.config.port}",
            f"--user-data-dir={self._user_data_dir}",
            *self.config.chrome_args,
            "about:blank",
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not await self._wait_for_cdp_ready():
            await self.stop_chrome()
            raise RuntimeError("Chrome failed to start with CDP.")

        return self._process

    async def connect_playwright(self) -> Page:
        if self._browser is not None:
            raise RuntimeError("Playwright is already connected.")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)

        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
        else:
            context = await self._browser.new_context()
            self._page = await context.new_page()

        return self._page

    async def stop_chrome(self) -> None:
        if self._process is not None:
            self._process.terminate()
            await self._process.wait()
            self._process = None

        if self._user_data_dir and Path(self._user_data_dir).exists():
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            self._user_data_dir = None

    async def disconnect_playwright(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            self._page = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def cleanup(self) -> None:
        await self.disconnect_playwright()
        await self.stop_chrome()

    async def __aenter__(self) -> "ChromeCDPManager":
        await self.start_chrome()
        await self.connect_playwright()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.cleanup()



