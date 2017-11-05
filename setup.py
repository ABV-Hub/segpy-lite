# setup.py
from distutils.core import setup
from setuptools import find_packages


CLASSIFIERS = [
    'Development Status :: 4 - Beta',
    'Intended Audience :: Developers',
    'Intended Audience :: Science/Research',
    'License :: OSI Approved :: MIT License',
    'Programming Language :: Python',
    'Topic :: Scientific/Engineering',
    'Topic :: Scientific/Engineering :: Mathematics',
    'Topic :: Scientific/Engineering :: Physics',
    'Operating System :: Microsoft :: Windows',
    'Operating System :: POSIX',
    'Operating System :: Unix',
    'Operating System :: MacOS',
    'Natural Language :: English',
]


setup(
    name='segpy-lite',
    version="1.0.1",
    description='Transfer of seismic data to and from SEG Y files',
    long_description="",
    url='https://github.com/whimian/segpy-lite',
    author='Yu Hao',
    author_email='yuhao89@live.cn',
    license='MIT',
    classifiers=CLASSIFIERS,
    keywords='seismic geocomputing geophysics',
    packages=find_packages(exclude=['test']),
    platforms=["Windows", "Linux", "Solaris", "Mac OS-X", "Unix"],
    zip_safe = False
)
