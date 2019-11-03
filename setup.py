from setuptools import setup

def readme():
    with open('README.md') as f:
        return f.read()

setup(
    name = 'intriniorealtime',
    packages = ['intriniorealtime'],
    version = '2.2.0',
    author = 'Intrinio Python SDK for Real-Time Stock & Crypto Prices',
    author_email = 'success@intrinio.com',
    url = 'https://intrinio.com',
    description = 'Intrinio Python SDK for Real-Time Stock & Crypto Prices',
    long_description = readme(),
    install_requires = ['requests==2.20.0','websocket-client==0.48.0'],
    python_requires = '~=3.6',
    download_url = 'https://github.com/intrinio/intrinio-realtime-python-sdk/archive/v2.0.0.tar.gz',
    keywords = ['realtime','stock prices','intrinio','stock market','stock data','financial'],
    classifiers = [
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial :: Investment'
    ]
)
