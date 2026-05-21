from setuptools import setup, find_packages

setup(
    name="meraki-engine",
    version="0.1.0",
    packages=find_packages(include=["primitive*", "engine*", "config*"]),
    install_requires=[
        "websockets>=12",
        "Pillow>=10",
    ],
    python_requires=">=3.10",
)
