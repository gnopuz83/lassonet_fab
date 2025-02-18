from setuptools import setup, find_packages

setup(
    name='lassonetfab',
    packages=find_packages(),
    url='https://github.com/gnopuz83/lassonet',
    description='This is a lassonet implementation',
    long_description=open('README.md').read(),
    dependency_links = [
     "git+git@github.com:gnopuz83/lassonet.git",
    ],
    package = ["lassonetfab"],
    include_package_data=True,
)