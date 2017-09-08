from setuptools import setup

def readme():
    with open('README.md') as f:
        return f.read()

setup(
    name = 'intriniorealtime',
    packages = ['intriniorealtime'],
    version = '1.0.1',
    author = 'Intrinio Python SDK for Real-Time Stock Prices',
    author_email = 'success@intrinio.co',
    url = 'https://intrinio.com',
    description = 'Intrinio Python SDK for Real-Time Stock Prices',
    long_description = readme(),
    install_requires = ['requests','websocket-client'],
    download_url = 'https://github.com/intrinio/intrinio-realtime-python-sdk/archive/1.0.0.tar.gz',
    keywords = ['realtime','stock prices','intrinio','stock market','stock data','financial'],
    classifiers = [
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial :: Investment'
    ]
)
