import io

from setuptools import setup


description = 'A high precision Java CMS optimizer'
try:
    with io.open('README.md', encoding="utf-8") as fh:
            long_description = fh.read()
except IOError:
    long_description = description

setup(
    name='jtune',
    version='4.0.1',
    description=description,
    long_description=description,
    url='https://github.com/linkedin/JTune',
    author='LinkedIn',
    author_email='jeward@linkedin.com',
    license='Apache',
    packages=['jtune'],
    entry_points={
        'console_scripts': [
            'jtune = jtune.jtune:main',
        ]
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ]
)
