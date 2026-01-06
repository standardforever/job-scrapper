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
from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)


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
        "--headless=new",
    ])


class ChromeCDPManager:
    def __init__(self, config: Optional[ChromeConfig] = None):
        self.config = config or ChromeConfig()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._user_data_dir: Optional[str] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        logger.debug(
            "ChromeCDPManager initialized",
            extra={
                "port": self.config.port,
                "startup_timeout": self.config.startup_timeout,
                "health_check_interval": self.config.health_check_interval,
            },
        )

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
        logger.debug(
            "Searching for Chrome executable",
            extra={"search_paths": self.config.chrome_paths},
        )
        for path in self.config.chrome_paths:
            if not os.path.exists(path) and path not in ["chrome", "chromium"]:
                logger.debug(
                    "Chrome path does not exist",
                    extra={"path": path},
                )
                continue

            try:
                proc = await asyncio.create_subprocess_exec(
                    path,
                    "--version",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                await proc.wait()
                logger.info(
                    "Chrome executable found",
                    extra={"path": path},
                )
                return path
            except Exception as e:
                logger.debug(
                    "Failed to verify Chrome executable",
                    extra={"path": path, "error": str(e)},
                )
                continue

        logger.error("Chrome executable not found in any search path")
        raise RuntimeError("Chrome not found. Please install Chrome or Chromium.")

    async def _wait_for_cdp_ready(self) -> bool:
        logger.debug(
            "Waiting for CDP to be ready",
            extra={
                "cdp_url": self.cdp_url,
                "startup_timeout": self.config.startup_timeout,
            },
        )
        timeout = aiohttp.ClientTimeout(total=self.config.health_check_timeout)

        for attempt in range(self.config.startup_timeout):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.cdp_url}/json/version",
                        timeout=timeout,
                    ) as response:
                        if response.status == 200:
                            logger.info(
                                "CDP is ready",
                                extra={"cdp_url": self.cdp_url, "attempts": attempt + 1},
                            )
                            return True
            except Exception as e:
                logger.debug(
                    "CDP health check failed, retrying",
                    extra={"attempt": attempt + 1, "error": str(e)},
                )
                pass

            await asyncio.sleep(self.config.health_check_interval)

        logger.error(
            "CDP failed to become ready within timeout",
            extra={
                "cdp_url": self.cdp_url,
                "startup_timeout": self.config.startup_timeout,
            },
        )
        return False
    
    
    async def is_cdp_port_active(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.cdp_url}/json/version", timeout=1):
                    return True
        except Exception:
            return False


    async def start_chrome(self) -> asyncio.subprocess.Process | None:
        logger.info("Starting Chrome with CDP")
        # 🔁 Reuse existing CDP session
        # if await self.is_cdp_port_active():
        #     logger.info(
        #         "CDP already active, reusing existing Chrome session",
        #         extra={"cdp_url": self.cdp_url},
        #     )
        #     self._owns_chrome = False
        #     return None
        if self._process is not None:
            logger.error("Attempted to start Chrome when already running")
            raise RuntimeError("Chrome is already running.")

        self._user_data_dir = tempfile.mkdtemp(prefix="chrome_cdp_")
        logger.debug(
            "Created temporary user data directory",
            extra={"user_data_dir": self._user_data_dir},
        )

        chrome_exe = await self._find_chrome_executable()

        cmd = [
            chrome_exe,
            f"--remote-debugging-port={self.config.port}",
            f"--user-data-dir={self._user_data_dir}",
            *self.config.chrome_args,
            "about:blank",
        ]
        logger.debug(
            "Chrome command prepared",
            extra={"command": cmd},
        )

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug(
            "Chrome process started",
            extra={"pid": self._process.pid},
        )

        if not await self._wait_for_cdp_ready():
            logger.error("Chrome failed to start with CDP, cleaning up")
            await self.stop_chrome()
            raise RuntimeError("Chrome failed to start with CDP.")

        logger.info(
            "Chrome started successfully with CDP",
            extra={"pid": self._process.pid, "cdp_url": self.cdp_url},
        )
        return self._process

    async def connect_playwright(self) -> Page:
        logger.info(
            "Connecting Playwright to CDP",
            extra={"cdp_url": self.cdp_url},
        )
        if self._browser is not None:
            logger.error("Attempted to connect Playwright when already connected")
            raise RuntimeError("Playwright is already connected.")

        self._playwright = await async_playwright().start()
        logger.debug("Playwright started")

        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
        logger.debug(
            "Playwright connected to CDP",
            extra={"cdp_url": self.cdp_url},
        )

        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
            logger.debug("Using existing page from browser context")
        else:
            context = await self._browser.new_context()
            self._page = await context.new_page()
            logger.debug("Created new browser context and page")

        logger.info("Playwright connected successfully")
        return self._page

    async def stop_chrome(self) -> None:
        logger.info("Stopping Chrome")
        if self._process is not None:
            pid = self._process.pid
            self._process.terminate()
            await self._process.wait()
            self._process = None
            logger.debug(
                "Chrome process terminated",
                extra={"pid": pid},
            )

        if self._user_data_dir and Path(self._user_data_dir).exists():
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            logger.debug(
                "Removed temporary user data directory",
                extra={"user_data_dir": self._user_data_dir},
            )
            self._user_data_dir = None

        logger.info("Chrome stopped successfully")

    async def disconnect_playwright(self) -> None:
        logger.info("Disconnecting Playwright")
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            self._page = None
            logger.debug("Browser closed")

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
            logger.debug("Playwright stopped")

        logger.info("Playwright disconnected successfully")

    async def cleanup(self) -> None:
        logger.info("Starting cleanup")
        await self.disconnect_playwright()
        await self.stop_chrome()
        logger.info("Cleanup completed")

    async def __aenter__(self) -> "ChromeCDPManager":
        logger.debug("Entering ChromeCDPManager context")
        await self.start_chrome()
        await self.connect_playwright()
        logger.debug("ChromeCDPManager context entered successfully")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        logger.debug(
            "Exiting ChromeCDPManager context",
            extra={
                "exc_type": str(exc_type) if exc_type else None,
                "exc_val": str(exc_val) if exc_val else None,
            },
        )
        await self.cleanup()
        logger.debug("ChromeCDPManager context exited")