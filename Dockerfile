FROM python:3.10

RUN mkdir /intrinio

WORKDIR /intrinio

COPY . /intrinio

RUN pip uninstall 'websocket'
RUN pip install 'websocket-client'
RUN pip install 'requests'
RUN pip install 'wsaccel'

RUN pip install 'intrinio_sdk'

CMD python example_app_equities.py
#CMD python example_app_options.py