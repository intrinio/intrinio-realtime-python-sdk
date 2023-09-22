from setuptools import setup

def readme():
    with open('README.md') as f:
        return f.read()

setup(
    name = 'intriniorealtime',
    packages = ['intriniorealtime'],
    version = '5.1.0',
    author = 'Intrinio Python SDK for Real-Time Stock Prices',
    author_email = 'success@intrinio.com',
    url = 'https://intrinio.com',
    description = 'Intrinio Python SDK for Real-Time Stock Prices',
    long_description = readme(),
    long_description_content_type = 'text/markdown',
    install_requires = ['requests>=2.26.0','websocket-client>=1.2.1','wsaccel>=0.6.3', 'intrinio-sdk>=6.26.0'],
    python_requires = '~=3.10',
    download_url = 'https://github.com/intrinio/intrinio-realtime-python-sdk/archive/v5.1.0.tar.gz',
    keywords = ['realtime','stock prices','intrinio','stock market','stock data','financial'],
    classifiers = [
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial :: Investment'
    ]
)