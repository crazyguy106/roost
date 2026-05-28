import pathlib
import re

from setuptools import setup, find_packages


def _read_version() -> str:
    init = pathlib.Path(__file__).parent / "roost" / "__init__.py"
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(), re.M)
    if not match:
        raise RuntimeError("Cannot find __version__ in roost/__init__.py")
    return match.group(1)


setup(
    name="roost",
    version=_read_version(),
    description="Build your AI nest — self-hosted productivity platform for AI agents",
    author="Roost Contributors",
    url="https://github.com/crazyguy106/roost",
    packages=find_packages(),
    install_requires=[],  # See requirements/
    entry_points={
        "console_scripts": [
            "roost-web=roost.web.app:main",
            "roost-bot=roost.bot.main:main",
            "roost-cli=roost.cli.main:main",
            "roost-mcp=roost.mcp.server:main",
            "roost-onboard=roost.cli.onboard:main",
        ],
    },
    python_requires=">=3.11",
)
