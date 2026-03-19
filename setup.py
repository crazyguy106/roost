from setuptools import setup, find_packages

setup(
    name="roost",
    version="0.1.0",
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
