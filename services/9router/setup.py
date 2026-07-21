from setuptools import setup, find_packages

setup(
    name="9router",
    version="1.0.0",
    description="Request router for pxx aider orchestration",
    author="pxx",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "httpx>=0.25.0",
    ],
    entry_points={
        "console_scripts": [
            "nine-router=nine_router_pkg.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
