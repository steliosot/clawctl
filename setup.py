from setuptools import setup


setup(
    name="clawctl",
    version="0.1.6",
    description="Simple manager and CLI for per-user OpenClaw Docker instances",
    py_modules=["api", "cli", "openclaw_manager", "clawctl"],
    python_requires=">=3.10",
    install_requires=[
        "fastapi==0.116.1",
        "uvicorn==0.35.0",
        "docker==7.1.0",
        "typer==0.16.1",
        "requests==2.32.4",
    ],
    entry_points={"console_scripts": ["clawctl=cli:main"]},
)
