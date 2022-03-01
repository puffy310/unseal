from setuptools import setup, find_packages

version = "0.0.1"


setup(
    name='Unseal',
    version=version,
    packages=find_packages(exclude=[]),
    python_requires='>=3.6.0',
    install_requires=[
        'torch>=1.10.1',
        'einops>=0.3.2',
        'numpy>=1.21.2',
        'transformers>=4.16.0',
        'tqdm',
        'matplotlib',
        'streamlit',
    ],
    # entry_points={
    #     'console_scripts': [
    #         '"unseal compare" = unseal.commands.interfaces.compare_two_inputs:main',
    #     ]
    # },
)
