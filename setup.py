from setuptools import setup

setup(
    name='jtune',
    version='2.0',
    description='A high precision Java CMS optimizer',
    url='https://github.com/linkedin/JTune',
    author='LinkedIn',
    license='Apache',
    packages=['jtune'],
    install_requires=[
        'argparse==1.4.0',
    ],
    entry_points={
        'console_scripts': [
            'jtune = jtune:main',
        ]
    }
)
