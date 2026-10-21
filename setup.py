import os
import re

from setuptools import setup, find_packages

HERE = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(HERE, "README.md"), "r", encoding="utf-8") as f:
    long_description = f.read()

# Single source of truth: indreelforge/_version.py
with open(os.path.join(HERE, "indreelforge", "_version.py"), "r", encoding="utf-8") as f:
    version = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)

setup(
    name="indreelforge",
    version=version,
    author="IndReelForge",
    author_email="support@indreelforge.com",
    description="Official Python SDK for IndReelForge — Social Media API to post to Instagram, YouTube, TikTok, Facebook, X, LinkedIn, Threads, Pinterest, Reddit, Bluesky & Google Business Profile",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://indreelforge.com",
    project_urls={
        "Documentation": "https://indreelforge.com/docs",
        "Source": "https://github.com/Indreelforge/indreelforge-python",
        "Bug Tracker": "https://github.com/Indreelforge/indreelforge-python/issues",
    },
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.28.0",
    ],
    extras_require={
        "test": ["pytest>=7"],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords=[
        "social-media-api", "instagram-api", "youtube-api", "tiktok-api",
        "facebook-api", "twitter-api", "linkedin-api", "threads-api",
        "pinterest-api", "reddit-api", "bluesky-api",
        "google-business-profile-api", "social-media-posting", "video-upload",
        "indreelforge", "mcp", "model-context-protocol", "ai-agents",
    ],
)
