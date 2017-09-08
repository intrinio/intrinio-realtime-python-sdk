from setuptools import setup, find_packages

setup(
    name = 'intriniorealtime',
    version = '1.0.0',
    author = 'Intrinio Python SDK for Real-Time Stock Prices',
    author_email = 'success@intrinio.co',
    url = 'https://intrinio.com',
    description = 'Intrinio ',
    long_description = open('README.md').read().strip(),
    packages = ['intriniorealtime',],
    install_requires=['requests','websocket-client'],
    keywords = ['realtime','stock prices','intrinio','stock market','stock data','financial'],
    classifiers = [
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial :: Investment'
    ]
)
